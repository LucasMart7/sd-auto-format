"""
auto_format_sd.py

Fica rodando no terminal observando quando um cartao SD e conectado ao PC
(Windows). Quando detecta um cartao removivel novo, formata automaticamente,
copia um arquivo de configuracao fixo para dentro dele, confere a gravacao
e ejeta o cartao.

Requisitos (instalar uma vez):
    pip install -r requirements.txt

IMPORTANTE: rode o terminal COMO ADMINISTRADOR, senao o format falha.
"""

import ctypes
import os
import shutil
import string
import subprocess
import time
from datetime import datetime

import win32com.client
import win32con
import win32file  # GetDriveType vive aqui, NAO em win32api
import winioctlcon

# ================== CONFIGURACOES (ajuste aqui) ==================

# Caminho do arquivo que sera copiado sempre que um cartao for detectado.
# O NOME IMPORTA: muitos dispositivos (ex.: cameras veiculares) so leem a
# configuracao de um arquivo com nome exato na raiz do cartao. shutil.copy
# preserva o nome de origem, entao nomeie o arquivo como o dispositivo espera.
ARQUIVO_A_COPIAR = r"C:\SDConfig\writeconfig.txt"

# Rotulo (nome) que o cartao vai receber depois de formatado (max. 11 chars p/ FAT32)
ROTULO_VOLUME = "SDCONFIG"

# Faixa de tamanho aceita, em GB. Protege contra formatar por engano
# um pendrive ou HD externo. Ajustado para cartao de 128 GB, que o Windows
# reporta como ~119 GiB. Alargue se usar cartoes de outra capacidade.
TAMANHO_MIN_GB = 100
TAMANHO_MAX_GB = 140

# Sistema de arquivos. None = automatico (FAT32 ate 32 GB, exFAT acima).
# O Windows NAO consegue criar FAT32 em volume maior que 32 GB; se o
# dispositivo exigir FAT32 num cartao de 128 GB, e preciso Rufus ou guiformat.
FS_FORCADO = None

# Arquivo de log
LOG_PATH = r"C:\SDConfig\log.txt"

# Intervalo entre verificacoes, em segundos
INTERVALO_SEGUNDOS = 2

# Quanto tempo esperar (segundos) o volume ficar pronto apos inserir/formatar
TIMEOUT_VOLUME_PRONTO = 30

# Ejetar o cartao automaticamente ao terminar (equivale a "Remover hardware
# com seguranca"). Deixe False se preferir tirar o cartao na mao.
EJETAR_AO_FINAL = True

# Abrir o Explorador de Arquivos na raiz do cartao ao terminar de gravar, para
# voce ver o arquivo aparecer. A janela e fechada sozinha antes da ejecao:
# uma janela aberta segura um handle no volume e faz o LOCK da ejecao falhar.
ABRIR_EXPLORER = True

# Quantos segundos deixar a janela do Explorer aberta antes de fechar e ejetar.
SEGUNDOS_EXPLORER = 6

# ===================================================================


def log(mensagem: str) -> None:
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} - {mensagem}"
    print(linha, flush=True)
    pasta = os.path.dirname(os.path.abspath(LOG_PATH))
    os.makedirs(pasta, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(linha + "\n")


def eh_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def desativar_quickedit() -> None:
    """Impede o console de congelar o script quando alguem clica na janela.

    Com QuickEdit ligado (padrao do PowerShell azul), um clique no console
    entra em modo de selecao e BLOQUEIA qualquer print ate apertar Enter/Esc.
    O loop segue rodando, mas trava no primeiro log("Cartao detectado...") -
    parece que o cartao nao foi reconhecido, e nada chega ao log.txt.
    """
    STD_INPUT_HANDLE = -10
    ENABLE_QUICK_EDIT_MODE = 0x0040
    ENABLE_EXTENDED_FLAGS = 0x0080
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    modo = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(modo)):
        return  # sem console (ex.: saida redirecionada) - nada a fazer
    novo = (modo.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS
    kernel32.SetConsoleMode(handle, novo)


def suprimir_dialogos_de_erro() -> None:
    """Evita o dialogo modal 'Nao ha disco na unidade' ao sondar o leitor vazio.

    Esse dialogo bloqueia a chamada (disk_usage) ate alguem clicar nele,
    muitas vezes escondido atras de outras janelas.
    """
    SEM_FAILCRITICALERRORS = 0x0001
    SEM_NOOPENFILEERRORBOX = 0x8000
    ctypes.windll.kernel32.SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX)


def status_no_titulo(texto: str) -> None:
    """Heartbeat no titulo da janela: mostra que o loop esta vivo sem poluir o log."""
    ctypes.windll.kernel32.SetConsoleTitleW(f"Monitor SD - {texto}")


def listar_cartoes_prontos() -> set[str]:
    """Letras de drive removiveis QUE TEM MIDIA INSERIDA E PRONTA.

    Nao basta olhar GetDriveType: um leitor de cartao vazio continua
    reportando DRIVE_REMOVABLE, com a letra sempre presente. O que muda
    quando o cartao entra e o volume ficar acessivel.
    """
    prontos = set()
    for letra in string.ascii_uppercase:
        raiz = f"{letra}:\\"
        # Sem try/except aqui de proposito: GetDriveType nao falha para uma
        # letra valida, e um except largo ja mascarou um AttributeError que
        # deixou esta funcao retornando conjunto vazio em silencio.
        if win32file.GetDriveType(raiz) != win32con.DRIVE_REMOVABLE:
            continue
        try:
            shutil.disk_usage(raiz)  # so funciona se houver midia pronta
        except OSError:
            continue  # leitor vazio / dispositivo nao pronto
        prontos.add(f"{letra}:")
    return prontos


def esperar_volume_pronto(drive: str, timeout: int = TIMEOUT_VOLUME_PRONTO) -> bool:
    """Aguarda o volume responder. Usado apos inserir e apos formatar."""
    limite = time.time() + timeout
    while time.time() < limite:
        try:
            shutil.disk_usage(drive + "\\")
            return True
        except OSError:
            time.sleep(0.5)
    return False


def tamanho_em_gb(drive: str) -> float:
    total, _, _ = shutil.disk_usage(drive + "\\")
    return total / (1024 ** 3)


def formatar(drive: str, fs: str) -> tuple[bool, str]:
    """Formata via PowerShell Format-Volume (nao interativo, ao contrario do format.com)."""
    letra = drive.rstrip(":")
    ps = (
        f"$ErrorActionPreference='Stop';"
        f"Format-Volume -DriveLetter {letra} -FileSystem {fs} "
        f"-NewFileSystemLabel '{ROTULO_VOLUME}' -Force -Confirm:$false | Out-Null;"
        f"exit 0"
    )
    resultado = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if resultado.returncode != 0:
        return False, (resultado.stderr or resultado.stdout or "").strip()
    return True, ""


def abrir_explorer(drive: str) -> None:
    """Abre o Explorador na raiz do cartao para inspecao visual."""
    try:
        os.startfile(drive + "\\")
    except Exception as e:
        log(f"AVISO: nao consegui abrir o Explorer em {drive}: {e}")


def fechar_janelas_explorer(drive: str) -> int:
    """Fecha janelas do Explorer abertas nesse drive. Retorna quantas fechou.

    Melhor esforco: rodando elevado, o COM pode nao enxergar as janelas do
    Explorer (que roda em integridade media), e ai retorna 0 sem erro. Por
    isso a ejecao nao depende disso dar certo - so fica mais rapida quando da.
    """
    alvo = f"file:///{drive}/".lower()
    fechadas = 0
    try:
        janelas = win32com.client.Dispatch("Shell.Application").Windows()
        for i in range(janelas.Count - 1, -1, -1):
            try:
                url = (janelas.Item(i).LocationURL or "").lower()
            except Exception:
                continue
            if url.startswith(alvo.rstrip("/")):
                try:
                    janelas.Item(i).Quit()
                    fechadas += 1
                except Exception:
                    pass
    except Exception as e:
        log(f"AVISO: nao consegui enumerar janelas do Explorer ({e}).")
    return fechadas


def ejetar_via_shell(drive: str) -> tuple[bool, str]:
    """Plano B: pede a ejecao ao proprio shell.

    Ao contrario do IOCTL, o shell coordena com o Explorer e consegue fechar
    as janelas dele antes de soltar o volume. Mais lento, porem mais tolerante
    a handles abertos.
    """
    try:
        ns = win32com.client.Dispatch("Shell.Application").Namespace(17)
        for item in ns.Items():
            if item.Path.rstrip("\\").upper() != drive.upper():
                continue
            for verbo in item.Verbs():
                nome = (verbo.Name or "").replace("&", "").lower()
                if nome.startswith("eject") or nome.startswith("ejetar"):
                    verbo.DoIt()
                    time.sleep(1.5)
                    return True, ""
            return False, "verbo de ejecao nao encontrado nesse volume"
        return False, f"{drive} nao aparece em 'Este Computador'"
    except Exception as e:
        return False, str(e)


def ejetar(drive: str, tentativas: int = 5) -> tuple[bool, str]:
    """Ejeta o volume com a mesma sequencia do 'Remover hardware com seguranca'.

    LOCK -> DISMOUNT -> liberar trava de midia -> EJECT. O LOCK e o passo que
    costuma falhar: basta o Explorer ou um antivirus ter um handle aberto no
    cartao. Por isso ele e repetido antes de desistir.
    """
    caminho = f"\\\\.\\{drive}"
    ultimo_erro = ""

    for _ in range(tentativas):
        handle = None
        try:
            handle = win32file.CreateFile(
                caminho,
                win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
                None,
                win32con.OPEN_EXISTING,
                0,
                None,
            )
            win32file.DeviceIoControl(handle, winioctlcon.FSCTL_LOCK_VOLUME, None, 0)
            win32file.DeviceIoControl(handle, winioctlcon.FSCTL_DISMOUNT_VOLUME, None, 0)
            # b"\x00" = PreventMediaRemoval FALSE: destrava a bandeja/slot
            win32file.DeviceIoControl(
                handle, winioctlcon.IOCTL_STORAGE_MEDIA_REMOVAL, b"\x00", 0
            )
            win32file.DeviceIoControl(handle, winioctlcon.IOCTL_STORAGE_EJECT_MEDIA, None, 0)
            return True, ""
        except Exception as e:
            ultimo_erro = str(e)
            time.sleep(1.0)
        finally:
            if handle is not None:
                try:
                    win32file.CloseHandle(handle)
                except Exception:
                    pass

    # O IOCTL falhou (quase sempre: handle aberto no volume). Tenta o shell,
    # que negocia com o Explorer em vez de exigir trava exclusiva.
    ok_shell, erro_shell = ejetar_via_shell(drive)
    if ok_shell:
        return True, ""

    return False, f"IOCTL: {ultimo_erro} | shell: {erro_shell}"


def formatar_e_copiar(drive: str) -> None:
    if not esperar_volume_pronto(drive):
        log(f"{drive} ignorado: volume nao ficou pronto a tempo.")
        return

    try:
        tam_gb = tamanho_em_gb(drive)
    except OSError as e:
        log(f"{drive} ignorado: nao foi possivel ler o tamanho ({e}).")
        return

    if not (TAMANHO_MIN_GB <= tam_gb <= TAMANHO_MAX_GB):
        log(f"{drive} ignorado: {tam_gb:.1f} GB fora da faixa "
            f"({TAMANHO_MIN_GB}-{TAMANHO_MAX_GB} GB).")
        return

    if not os.path.isfile(ARQUIVO_A_COPIAR):
        log(f"ERRO: arquivo a copiar nao encontrado em {ARQUIVO_A_COPIAR}. "
            f"{drive} NAO sera formatado.")
        return

    fs = FS_FORCADO or ("FAT32" if tam_gb <= 32 else "exFAT")
    if fs == "FAT32" and tam_gb > 32:
        log(f"ERRO: FAT32 pedido para {drive} ({tam_gb:.1f} GB). O Windows nao "
            f"formata FAT32 acima de 32 GB. Use Rufus/guiformat ou deixe "
            f"FS_FORCADO = None para cair em exFAT.")
        return

    log(f"Cartao detectado em {drive} ({tam_gb:.1f} GB). Formatando em {fs}...")

    ok, erro = formatar(drive, fs)
    if not ok:
        log(f"ERRO ao formatar {drive}: {erro}")
        return

    if not esperar_volume_pronto(drive):
        log(f"ERRO: {drive} nao voltou a ficar acessivel depois do format.")
        return

    destino = os.path.join(drive + "\\", os.path.basename(ARQUIVO_A_COPIAR))
    try:
        shutil.copy(ARQUIVO_A_COPIAR, destino)
    except Exception as e:
        log(f"ERRO ao copiar arquivo para {drive}: {e}")
        return

    # Conferir ANTES de ejetar: depois da ejecao nao ha mais como verificar.
    # Cartao SD confirma escrita que ficou so no cache e nunca chegou ao flash.
    try:
        with open(ARQUIVO_A_COPIAR, "rb") as f:
            origem_bytes = f.read()
        with open(destino, "rb") as f:
            destino_bytes = f.read()
    except Exception as e:
        log(f"ERRO ao reler {destino} para conferencia: {e}")
        return

    if origem_bytes != destino_bytes:
        log(f"ERRO: {destino} gravado diferente da origem "
            f"({len(destino_bytes)} vs {len(origem_bytes)} bytes). "
            f"NAO ejetado - inspecione o cartao.")
        return

    log(f"Concluido: {drive} formatado ({fs}), "
        f"{os.path.basename(destino)} gravado e conferido "
        f"({len(origem_bytes)} bytes).")

    if ABRIR_EXPLORER:
        log(f"Abrindo {drive} no Explorer por {SEGUNDOS_EXPLORER}s "
            f"para voce conferir...")
        abrir_explorer(drive)
        time.sleep(SEGUNDOS_EXPLORER)
        if EJETAR_AO_FINAL:
            fechadas = fechar_janelas_explorer(drive)
            log(f"Fechando Explorer antes de ejetar ({fechadas} janela(s)).")
            time.sleep(1.0)  # o Explorer leva um instante para soltar o handle

    if EJETAR_AO_FINAL:
        ok, erro = ejetar(drive)
        if ok:
            log(f"{drive} ejetado com seguranca. Pode remover o cartao.")
        else:
            log(f"AVISO: {drive} gravado corretamente, mas a ejecao falhou "
                f"({erro}). Remova pelo icone da bandeja do Windows.")


def main() -> None:
    if not eh_admin():
        log("ERRO: execute este script em um terminal COMO ADMINISTRADOR. "
            "Sem elevacao o format sempre falha com acesso negado.")
        return

    desativar_quickedit()
    suprimir_dialogos_de_erro()

    log("=== Monitor de cartao SD iniciado. Aguardando insercao... ===")
    log("(Deixe este terminal aberto. Pressione Ctrl+C para parar.)")

    # Cartoes ja inseridos ao iniciar NAO sao formatados.
    cartoes_conhecidos = listar_cartoes_prontos()
    if cartoes_conhecidos:
        log(f"Ignorando cartoes ja presentes: {', '.join(sorted(cartoes_conhecidos))}. "
            f"Para formatar, remova e reinsira o cartao (nao precisa reiniciar o script).")

    try:
        while True:
            time.sleep(INTERVALO_SEGUNDOS)
            cartoes_atuais = listar_cartoes_prontos()
            status_no_titulo(f"aguardando cartao ({datetime.now():%H:%M:%S})")

            for drive in sorted(cartoes_atuais - cartoes_conhecidos):
                status_no_titulo(f"processando {drive}")
                formatar_e_copiar(drive)

            cartoes_conhecidos = cartoes_atuais
    except KeyboardInterrupt:
        log("Monitor encerrado pelo usuario.")


if __name__ == "__main__":
    main()

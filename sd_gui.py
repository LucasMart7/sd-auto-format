"""
sd_gui.py

Interface grafica para o auto_format_sd.py. Mesma automacao, sem terminal:
abre uma janela com botao Iniciar/Parar e o log ao vivo.

Abra pelo atalho "Monitor SD" na Area de Trabalho, ou com:
    pythonw sd_gui.py

Pede elevacao (UAC) sozinho ao abrir: sem administrador o format falha.
"""

import ctypes
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import pythoncom

import auto_format_sd as afs

COR_PARADO = "#808080"
COR_AGUARDANDO = "#1a7f37"
COR_PROCESSANDO = "#c76b00"


def relancar_como_admin() -> None:
    """Reabre este script elevado (prompt do UAC) e encerra o processo atual."""
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.isfile(pythonw):
        exe = pythonw  # sem janela de console atras da interface
    script = os.path.abspath(__file__)
    ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, f'"{script}"', None, 1)
    if ret <= 32:  # usuario recusou o UAC ou falhou ao abrir
        tk.Tk().withdraw()  # sem isso o messagebox abre uma janela vazia junto
        messagebox.showerror(
            "Monitor SD",
            "O monitor precisa rodar como administrador para formatar cartoes.\n"
            "Abra de novo e clique em 'Sim' no aviso do Windows.",
        )
    sys.exit(0)


class MonitorSD:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.fila = queue.Queue()
        self.parar = threading.Event()
        self.thread = None
        self.processando = False
        self.gravados = 0

        afs.OUVINTES_LOG.append(lambda linha: self.fila.put(("log", linha)))

        root.title("Monitor SD")
        root.geometry("760x520")
        root.minsize(560, 380)
        root.protocol("WM_DELETE_WINDOW", self.fechar)

        self._montar_tela()
        self._mostrar_status("Parado", COR_PARADO)
        self.root.after(100, self._processar_fila)

    # ------------------------------------------------------------ tela

    def _montar_tela(self) -> None:
        topo = ttk.Frame(self.root, padding=(12, 12, 12, 6))
        topo.pack(fill="x")

        self.bolinha = tk.Canvas(topo, width=18, height=18, highlightthickness=0)
        self.bolinha.pack(side="left")
        self.circulo = self.bolinha.create_oval(2, 2, 16, 16, fill=COR_PARADO, outline="")
        self.lbl_status = ttk.Label(topo, font=("Segoe UI", 14, "bold"))
        self.lbl_status.pack(side="left", padx=(8, 0))

        self.lbl_contador = ttk.Label(topo, font=("Segoe UI", 10))
        self.lbl_contador.pack(side="right")
        self._atualizar_contador()

        info = ttk.Frame(self.root, padding=(12, 0, 12, 6))
        info.pack(fill="x")
        existe = os.path.isfile(afs.ARQUIVO_A_COPIAR)
        ttk.Label(
            info,
            text=f"Arquivo copiado: {afs.ARQUIVO_A_COPIAR}"
            + ("" if existe else "   (NAO ENCONTRADO - nenhum cartao sera formatado)"),
            foreground="" if existe else "#b00020",
        ).pack(anchor="w")
        ttk.Label(
            info,
            text=f"Cartoes aceitos: {afs.TAMANHO_MIN_GB}-{afs.TAMANHO_MAX_GB} GB   |   "
            f"Rotulo: {afs.ROTULO_VOLUME}   |   Cartoes ja inseridos ao iniciar sao ignorados",
            foreground="#555555",
        ).pack(anchor="w")

        opcoes = ttk.Frame(self.root, padding=(12, 0, 12, 6))
        opcoes.pack(fill="x")
        self.var_ejetar = tk.BooleanVar(value=afs.EJETAR_AO_FINAL)
        self.var_explorer = tk.BooleanVar(value=afs.ABRIR_EXPLORER)
        ttk.Checkbutton(
            opcoes, text="Ejetar ao terminar", variable=self.var_ejetar,
            command=self._aplicar_opcoes,
        ).pack(side="left")
        ttk.Checkbutton(
            opcoes, text="Abrir Explorer para conferir", variable=self.var_explorer,
            command=self._aplicar_opcoes,
        ).pack(side="left", padx=(16, 0))

        botoes = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        botoes.pack(fill="x")
        self.btn_iniciar = ttk.Button(botoes, text="Iniciar monitor", command=self.alternar)
        self.btn_iniciar.pack(side="left", ipadx=10, ipady=4)
        ttk.Button(botoes, text="Abrir log.txt", command=self.abrir_log).pack(side="right")
        ttk.Button(botoes, text="Limpar tela", command=self.limpar).pack(side="right", padx=(0, 8))

        self.txt = ScrolledText(self.root, font=("Consolas", 9), state="disabled", wrap="word")
        self.txt.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.txt.tag_config("erro", foreground="#b00020")
        self.txt.tag_config("aviso", foreground="#c76b00")
        self.txt.tag_config("ok", foreground="#1a7f37")

    def _mostrar_status(self, texto: str, cor: str) -> None:
        self.lbl_status.config(text=texto)
        self.bolinha.itemconfig(self.circulo, fill=cor)

    def _atualizar_contador(self) -> None:
        self.lbl_contador.config(text=f"Cartoes gravados nesta sessao: {self.gravados}")

    def _escrever(self, linha: str) -> None:
        tag = ()
        if "ERRO" in linha:
            tag = ("erro",)
        elif "AVISO" in linha:
            tag = ("aviso",)
        elif "Concluido:" in linha or "ejetado com seguranca" in linha:
            tag = ("ok",)
        self.txt.config(state="normal")
        self.txt.insert("end", linha + "\n", tag)
        self.txt.see("end")
        self.txt.config(state="disabled")

    def _aplicar_opcoes(self) -> None:
        # formatar_e_copiar le essas globais a cada cartao, entao vale na hora.
        afs.EJETAR_AO_FINAL = self.var_ejetar.get()
        afs.ABRIR_EXPLORER = self.var_explorer.get()

    # ------------------------------------------------------------ acoes

    def alternar(self) -> None:
        if self.thread and self.thread.is_alive():
            self.parar.set()
            self.btn_iniciar.config(state="disabled", text="Parando...")
            return
        self.parar.clear()
        self.thread = threading.Thread(target=self._monitorar, daemon=True)
        self.thread.start()
        self.btn_iniciar.config(text="Parar monitor")
        self._mostrar_status("Aguardando cartao...", COR_AGUARDANDO)

    def abrir_log(self) -> None:
        if os.path.isfile(afs.LOG_PATH):
            os.startfile(afs.LOG_PATH)
        else:
            messagebox.showinfo("Monitor SD", f"Ainda nao existe log em {afs.LOG_PATH}.")

    def limpar(self) -> None:
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.config(state="disabled")

    def fechar(self) -> None:
        if self.processando and not messagebox.askyesno(
            "Monitor SD",
            "Um cartao esta sendo formatado/gravado agora.\n"
            "Fechar no meio pode deixar o cartao inutilizavel ate formatar de novo.\n\n"
            "Fechar mesmo assim?",
        ):
            return
        self.parar.set()
        self.root.destroy()

    # ------------------------------------------------------------ thread

    def _monitorar(self) -> None:
        """Mesmo loop do main() do auto_format_sd, interrompivel pelo botao Parar."""
        pythoncom.CoInitialize()  # win32com (Explorer/ejecao) exige isso por thread
        try:
            afs.log("=== Monitor de cartao SD iniciado. Aguardando insercao... ===")
            conhecidos = afs.listar_cartoes_prontos()
            if conhecidos:
                afs.log(f"Ignorando cartoes ja presentes: {', '.join(sorted(conhecidos))}. "
                        f"Para formatar, remova e reinsira o cartao.")
            while not self.parar.wait(afs.INTERVALO_SEGUNDOS):
                atuais = afs.listar_cartoes_prontos()
                for drive in sorted(atuais - conhecidos):
                    if self.parar.is_set():
                        break
                    self.fila.put(("processando", drive))
                    afs.formatar_e_copiar(drive)
                    self.fila.put(("aguardando", None))
                conhecidos = atuais
        except Exception as e:
            afs.log(f"ERRO inesperado no monitor: {e!r}")
        finally:
            afs.log("Monitor parado.")
            pythoncom.CoUninitialize()
            self.fila.put(("parado", None))

    def _processar_fila(self) -> None:
        try:
            while True:
                tipo, valor = self.fila.get_nowait()
                if tipo == "log":
                    self._escrever(valor)
                    if "Concluido:" in valor:
                        self.gravados += 1
                        self._atualizar_contador()
                elif tipo == "processando":
                    self.processando = True
                    self._mostrar_status(f"Processando {valor} - nao remova o cartao", COR_PROCESSANDO)
                elif tipo == "aguardando":
                    self.processando = False
                    if not self.parar.is_set():
                        self._mostrar_status("Aguardando cartao...", COR_AGUARDANDO)
                elif tipo == "parado":
                    self.processando = False
                    self._mostrar_status("Parado", COR_PARADO)
                    self.btn_iniciar.config(state="normal", text="Iniciar monitor")
        except queue.Empty:
            pass
        self.root.after(100, self._processar_fila)


def main() -> None:
    if not afs.eh_admin():
        relancar_como_admin()
    afs.suprimir_dialogos_de_erro()
    root = tk.Tk()
    MonitorSD(root)
    root.mainloop()


if __name__ == "__main__":
    main()

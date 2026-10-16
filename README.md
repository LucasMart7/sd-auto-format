# SD Auto Format

Script em Python para Windows que prepara cartões SD em lote sem intervenção manual. Ele fica observando o leitor de cartão e, quando um cartão é inserido, faz tudo sozinho:

1. Detecta o cartão novo (só mídia removível e pronta, ignora leitor vazio).
2. Confere se o tamanho está na faixa esperada, para nunca formatar um pendrive ou HD externo por engano.
3. Formata o cartão (FAT32 até 32 GB, exFAT acima) via `Format-Volume`.
4. Copia o arquivo de configuração para a raiz do cartão.
5. Relê o arquivo e compara byte a byte com a origem antes de liberar o cartão.
6. Ejeta com segurança (mesma sequência do "Remover hardware com segurança").

Cada etapa vai para um log com data e hora.

## Por que fiz

No recondicionamento de dispositivos, cada cartão precisava ser formatado, receber um arquivo de configuração com nome exato e ser ejetado. Feito à mão, são vários cliques por cartão e é fácil esquecer um passo ou gravar no drive errado. Com o script, a pessoa só insere o cartão, espera o aviso e tira.

## Detalhes técnicos que deram trabalho

- **Leitor vazio parece cartão.** O leitor continua aparecendo como `DRIVE_REMOVABLE` mesmo sem cartão. A detecção usa `GetDriveType` e também testa se o volume responde (`disk_usage`).
- **Diálogo "Não há disco na unidade".** Ao sondar o leitor vazio, o Windows abre um diálogo modal que trava o script. Isso é evitado com `SetErrorMode`.
- **Console congelado.** Com o QuickEdit ligado, um clique na janela do terminal pausa o `print` e o script parece travado. O QuickEdit é desligado na inicialização.
- **Gravação que não chegou ao flash.** O arquivo é relido e comparado com a origem antes da ejeção, porque depois de ejetar não dá mais para conferir.
- **Ejeção que falha.** Basta o Explorer ter um handle aberto para o `FSCTL_LOCK_VOLUME` falhar. O script tenta o IOCTL (LOCK, DISMOUNT, liberar trava, EJECT) algumas vezes e, se não der, pede a ejeção ao Shell do Windows, que negocia com o Explorer.

## Como usar

Requisitos: Windows, Python 3.9+ e terminal aberto **como administrador**.

```bash
pip install -r requirements.txt
python auto_format_sd.py
```

Antes de rodar, ajuste o bloco de configurações no topo do script:

| Variável | O que faz |
| --- | --- |
| `ARQUIVO_A_COPIAR` | Arquivo copiado para cada cartão. O nome é mantido. |
| `ROTULO_VOLUME` | Nome do volume após a formatação. |
| `TAMANHO_MIN_GB` / `TAMANHO_MAX_GB` | Faixa de tamanho aceita. Fora dela o cartão é ignorado. |
| `FS_FORCADO` | `None` escolhe sozinho; ou force `"FAT32"` / `"exFAT"`. |
| `EJETAR_AO_FINAL` | Ejeta o cartão ao terminar. |
| `ABRIR_EXPLORER` | Mostra o conteúdo do cartão por alguns segundos antes de ejetar. |

Cartões que já estavam inseridos quando o script começou não são formatados. Para processá-los, basta remover e inserir de novo.

> **Atenção:** o script apaga todo o conteúdo do cartão. Confira a faixa de tamanho antes de usar.

## Tecnologias

Python, pywin32 (Win32 API, IOCTLs de armazenamento, COM do Shell), PowerShell e ctypes.

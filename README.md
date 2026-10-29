# Connect Linux

Liga um computador Linux (feito para o Linux Mint) a um celular Android. Os dois aparelhos se encontram sozinhos, pedem um código de pareamento e, depois disso, trocam texto, arquivos, área de transferência e notificações. Funciona por Wi-Fi, Bluetooth, cabo USB ou pela internet com o Tailscale.

## Download

| Aparelho | Arquivo |
| --- | --- |
| Computador (Linux Mint, Ubuntu, Debian) | [connectlinux_all.deb](https://github.com/lhcabral/connect_linux/releases/latest/download/connectlinux_all.deb) |
| Celular (Android 8 ou mais novo) | [ConnectLinux.apk](https://github.com/lhcabral/connect_linux/releases/latest/download/ConnectLinux.apk) |

Todas as versões e as somas SHA-256 ficam na página de [releases](https://github.com/lhcabral/connect_linux/releases).

## Como instalar

### No computador

**Pela interface:** baixe o `connectlinux_all.deb`, dê dois cliques no arquivo e clique em **Instalar pacote**. O Mint pede a sua senha e instala junto o que falta.

**Pelo terminal:**

```bash
cd ~/Downloads
wget https://github.com/lhcabral/connect_linux/releases/latest/download/connectlinux_all.deb
sudo apt install ./connectlinux_all.deb
```

Depois abra o **Connect Linux** pelo menu do Mint, ou rode `connectlinux` no terminal.

Se o firewall estiver ligado, libere as portas do Wi-Fi:

```bash
sudo ufw allow 17170/udp
sudo ufw allow 17171/tcp
```

Para desinstalar: `sudo apt remove connectlinux`.

### No celular

1. Abra no celular o link do [ConnectLinux.apk](https://github.com/lhcabral/connect_linux/releases/latest/download/ConnectLinux.apk) e baixe o arquivo.
2. Toque no arquivo baixado. Na primeira vez, o Android pede para **permitir instalar apps desta fonte**: ative para o navegador ou o gerenciador de arquivos e volte.
3. Toque em **Instalar**. Se o Play Protect avisar que o app é desconhecido, toque em **Mais detalhes** e depois em **Instalar assim mesmo**. Isso acontece com todo app instalado fora da Play Store.
4. Abra o Connect Linux e aceite as notificações.

Com o celular no cabo USB e a depuração ligada, também dá para instalar pelo computador: `adb install ConnectLinux.apk`.

O aplicativo tem três abas: **Início** (status, computadores e envio), **Conectar** (IP, Bluetooth e cabo USB) e **Ajustes**.

Para encaminhar as notificações, vá em **Ajustes**, toque em **Acesso às notificações** e ative o Connect Linux. Em **Apps que enviam notificações**, escolha quais mensageiros e redes sociais instalados no celular mandam notificações para o computador. Em **Manter conexão em segundo plano**, retire a restrição de bateria do aplicativo, senão o Android pode derrubar a conexão.

Quando sai uma versão nova, o aplicativo avisa ao abrir e oferece **Atualizar**. Ele baixa o APK da página de releases, confere a soma SHA-256 e abre o instalador do Android. Na primeira vez, o Android pede para permitir que o Connect Linux instale apps. Para procurar na hora, toque na versão em **Ajustes > Sobre**.

## Como parear

1. Abra o Connect Linux no computador e no celular.
2. Escolha como conectar:
   - **Wi-Fi:** com os dois na mesma rede, toque em **Parear** no computador que aparece na lista. Se ele não aparecer, digite em **Conectar pelo IP** o endereço mostrado na janela do computador (porta 17171).
   - **Bluetooth**, **cabo USB** ou **Tailscale:** veja [Quando o Wi-Fi não funciona](#quando-o-wi-fi-não-funciona).
3. Digite no celular o código de 6 dígitos que apareceu no computador.

Depois do primeiro pareamento, o celular reconecta sozinho pelo mesmo caminho.

## O que dá para fazer

- Enviar texto, link e arquivo nos dois sentidos.
- Sincronizar a área de transferência.
- Mostrar no computador as notificações do celular.
- Ver a bateria e chamar o outro aparelho (som no computador, vibração no celular).
- Compartilhar do celular pelo menu Compartilhar do Android.
- Abrir uma imagem na tela do outro aparelho (veja abaixo).
- Iniciar junto com o Linux, só com o ícone ao lado do relógio. Vem ligado depois da primeira vez que o programa é aberto; para desligar, desmarque **Iniciar com o Linux** na janela.

### Abrir uma imagem na tela do outro aparelho

- **Do computador para o celular:** clique com o botão direito numa imagem no gerenciador de arquivos e escolha **Enviar para o Android "Nome do celular"**. Com a imagem aberta no Visualizador de imagens, a opção fica em **Abrir com**. A opção aparece para cada celular pareado enquanto o Connect Linux estiver rodando, e o celular conecta sozinho se estiver desconectado.
- **Do celular para o computador:** na Galeria, toque em Compartilhar e escolha **Abrir no computador**. A imagem abre no visualizador padrão do Linux.

Para a imagem abrir sozinha no celular mesmo com o aplicativo em segundo plano, toque em **Abrir na tela as imagens do computador** e permita "Sobrepor a outros apps". Sem essa permissão, aparece uma notificação: toque nela para ver a imagem.

Arquivos recebidos no computador vão para `~/ConnectLinux/Recebidos`. No celular, vão para a pasta Downloads. O limite é 100 MB por arquivo.

O tráfego pelo Wi-Fi, Bluetooth e cabo USB não é criptografado pelo aplicativo. Pareie só em redes em que você confia. Pelo Tailscale, a conexão é criptografada.

## Quando o Wi-Fi não funciona

Redes de empresa, faculdade ou visitante costumam isolar os aparelhos entre si. Nesse caso, use uma destas formas. O pareamento por código é o mesmo em todas.

### Bluetooth

1. No computador, clique em **Ligar Bluetooth e ficar visível**. O computador fica visível por 3 minutos.
2. No celular, abra as configurações de Bluetooth do Android, procure o computador e pareie, confirmando o código nos dois aparelhos. Isso só precisa ser feito uma vez.
3. No Connect Linux do celular, toque em **Conectar por Bluetooth** e escolha o computador.

É mais lento que o Wi-Fi, mas serve bem para texto, área de transferência, notificações e arquivos pequenos.

### Cabo USB

1. No celular, ative as **Opções do desenvolvedor** (toque 7 vezes em "Número da versão" em Configurações > Sobre o telefone) e ligue a **Depuração USB**.
2. Instale o `adb` no computador: `sudo apt install adb`.
3. Conecte o cabo e aceite o aviso de depuração que aparece no celular.
4. Quando a janela do computador mostrar **Cabo USB: pronto**, toque em **Conectar por cabo USB** no celular.

### Pela internet (Tailscale)

O [Tailscale](https://tailscale.com) cria uma rede privada e criptografada entre os seus aparelhos, gratuita para uso pessoal. Funciona em qualquer rede, inclusive com o celular nos dados móveis.

1. No computador:

   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo apt install tailscale
   sudo tailscale up
   ```

   O `tailscale up` mostra um link: abra no navegador e entre com a sua conta.
2. No celular: instale o Tailscale pela Play Store e entre com a mesma conta.
3. Veja o IP do computador com `tailscale ip -4` (começa com `100.`).
4. No Connect Linux do celular, digite esse IP em **Conectar pelo IP**.

## Limites

- Pelo Wi-Fi, os dois aparelhos precisam se enxergar na rede. Se não se enxergarem, use Bluetooth, cabo USB ou Tailscale.
- A área de transferência do Android só é lida com o aplicativo aberto, ou pelo botão **Enviar área de transferência**. O computador pode atualizar a área de transferência do celular a qualquer momento.
- Não há acesso remoto a mouse, teclado, SMS ou terminal. O celular não executa comandos no Linux.

## Desenvolvimento

### Computador

O programa é Python puro (3.10 ou mais novo), na pasta `desktop`.

```bash
cd desktop
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-dbus python3-qrcode xclip libnotify-bin
./run.sh              # abre a janela
./run.sh --headless   # roda no terminal, sem janela
python3 -m unittest discover -s tests
```

`./install.sh` coloca no menu do Mint a versão da pasta, sem empacotar. Para gerar o `.deb` em `dist/`:

```bash
desktop/packaging/build-deb.sh
```

### Celular

Abra a pasta `android` no Android Studio, ou compile pelo terminal:

```bash
cd android
./gradlew :app:assembleDebug     # APK de teste em app/build/outputs/apk/debug/
./gradlew :app:assembleRelease   # APK de lançamento em app/build/outputs/apk/release/
```

O projeto usa o plugin Android 9.4 e o Gradle 9.8, e compila com o Java que vem no Android Studio.

A versão do APK vem de `__version__` em `desktop/connectlinux/__init__.py`, a mesma que dá nome à release. O aviso de atualização compara essa versão com a tag da última release, então o APK publicado precisa ser compilado com a versão da tag.

O APK de lançamento é assinado com a chave definida em `~/.gradle/gradle.properties`:

```properties
CONNECTLINUX_STORE_FILE=/caminho/para/connectlinux-release.jks
CONNECTLINUX_STORE_PASSWORD=...
CONNECTLINUX_KEY_ALIAS=connectlinux
CONNECTLINUX_KEY_PASSWORD=...
```

Sem essas linhas, o `assembleRelease` gera um APK sem assinatura. Guarde a chave: as atualizações do app precisam ser assinadas com ela, senão o Android exige desinstalar a versão anterior.

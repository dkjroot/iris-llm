# Talk to Iris from your phone

If you have your phone handy, you can open Iris in its browser and type a message
or tap **Microphone** and just start talking. Iris sends the spoken reply back
to the phone, then listens again. Tap the button again to switch the mic off.
Typed messages get a written reply. On HTTPS, tick **Remember this phone** to
keep the connection key in this browser after a successful turn. Untick it
under **Connection key** to remove the saved copy. The page keeps the transcript
in the open tab, and Iris still follows the server's usual memory and private-mode settings.

Tap Iris's face to open the same menu as on the robot. **Command list** is handy
when you remember what you want Iris to do but have forgotten the words for it.
You can leave the list open while you speak. Under **Personalities**, pick
**Assistant**, **Friend** or **Comedian**. New tabs start with Assistant, which
keeps things straightforward when you're just trying to turn a light on.
The selection belongs to the tab you're using. To use your own personalities,
add `--web-personalities-file PATH` to the server command. It takes the same
JSON format as the robot's `--personalities-file`.

Tap the smiley in the top left to try a different face. Your browser
remembers the one you picked for the next time you open Iris.

On the computer that runs Iris, add `--web-chat` to the existing server command
and restart it. For example:

```sh
python iris.py server --host 0.0.0.0 --web-chat
```

Connect your phone to your home Wi-Fi and open
`http://192.168.1.10:8765/chat`, replacing `192.168.1.10` with the server's
LAN address. Enter the same connection key you set as `IRIS_DROID_TOKEN`.
If the server firewall only allows the Pi, allow the phone too. Plain HTTP
is enough for a quick text test on trusted Wi-Fi, though it sends the key
and messages without encryption.

## Enable the phone microphone

The browser needs an HTTPS address it trusts before it can use the microphone.
If you already have an HTTPS reverse proxy on the server, point it at Iris on
`127.0.0.1:8765` and open `/chat` through it. If you don't, you can run
[Caddy](https://caddyserver.com/docs/quick-starts/reverse-proxy) on the same
computer. Save this example as `Caddyfile` and replace both occurrences of
`192.168.1.10` with your server's LAN address:

```caddyfile
{
    auto_https disable_redirects
}

https://192.168.1.10:8443 {
    bind 192.168.1.10
    tls internal
    reverse_proxy 127.0.0.1:8765
}
```

Start it with `caddy run --config Caddyfile --adapter caddyfile`.
If you saved it as `config.conf`, use
`caddy run --config config.conf --adapter caddyfile`. The adapter tells Caddy
to read Caddyfile syntax even with a different filename. If you leave it out
with `config.conf`, Caddy tries to read the file as JSON.
[Caddy command-line guide](https://caddyserver.com/docs/command-line).

If your setup expects JSON, save the example below as `caddy.json`. Replace
all three occurrences of `192.168.1.10` with your server's LAN address, then run
`caddy run --config caddy.json`.

```json
{
  "apps": {
    "http": {
      "servers": {
        "iris": {
          "listen": ["192.168.1.10:8443"],
          "automatic_https": {"disable_redirects": true},
          "tls_connection_policies": [{}],
          "routes": [
            {
              "match": [{"host": ["192.168.1.10"]}],
              "handle": [
                {
                  "handler": "reverse_proxy",
                  "upstreams": [{"dial": "127.0.0.1:8765"}]
                }
              ]
            }
          ]
        }
      }
    },
    "tls": {
      "automation": {
        "policies": [
          {
            "subjects": ["192.168.1.10"],
            "issuers": [{"module": "internal"}]
          }
        ]
      }
    }
  }
}
```

Allow TCP port 8443 from your home network in the server firewall. Follow
[Caddy's guide to finding its local root certificate](https://caddyserver.com/docs/running)
and install that certificate on the phone. On iPhone, there's a separate step
after the profile is installed. Go to **Settings → General → About → Certificate
Trust Settings** and turn on the switch for the Caddy root under **Enable Full
Trust for Root Certificates**. If the certificate is already listed there,
check that its switch is on. [Apple's certificate trust instructions](https://support.apple.com/en-us/102390).

Open `https://192.168.1.10:8443/chat` and allow microphone access
when the browser asks. Keep these ports on the home network and leave router
port forwarding off. The Pi can keep using its existing server address.

### Running Caddy in Ubuntu on WSL2

If Caddy logs `failed to install root certificate` with a `keytool.exe` error,
it had trouble adding the certificate to a local trust store. If Caddy stays
running, it can still serve HTTPS. To skip the automatic trust install, add
`skip_install_trust` alongside `auto_https disable_redirects` in the top block
of the Caddyfile and restart Caddy. You'll still need to trust the certificate
on your phone. [Caddy's local HTTPS guide](https://caddyserver.com/docs/automatic-https).

If you can't find `~/.local/share/caddy`, ask Caddy where it stores its files.
Use the same executable and user you started Caddy with. If you started it
with `sudo`, use `sudo caddy environ` in the second command.

```sh
type -a caddy
caddy environ | grep '^caddy.AppDataDir='
```

Look for `pki/authorities/local/root.crt` inside the directory it prints.
If you're running `caddy.exe` from WSL and it prints a Windows path, pass that
path to `wslpath -u 'C:\path\from\Caddy'` to open it from Ubuntu. If you
installed Caddy as Ubuntu's packaged systemd service, its data directory is
usually `/var/lib/caddy/.local/share/caddy`. Copy just `root.crt` to the phone
and follow the trust steps above. [Caddy data directory guide](https://caddyserver.com/docs/conventions).

If the phone times out, check how Windows exposes the WSL listener. With
WSL2's default NAT networking, remove the `bind` line from the Caddyfile so
Caddy can listen inside WSL. Keep the Windows LAN IP in the site address, and
forward TCP port 8443 from Windows to WSL. With mirrored networking, check
that the Hyper-V firewall allows inbound TCP 8443 from your LAN.
[Microsoft's WSL networking guide](https://learn.microsoft.com/en-us/windows/wsl/networking).

## Talk to Iris with Siri

This is handy when you've got your hands full and want to ask Iris about the
living room lamp. Say **“Hey Siri, Iris Relay”**, wait for the prompt, then say
**“Turn on the living room lamp”** or whatever you'd normally ask Iris. The
shortcut sends what you said as text, then your iPhone reads Iris's reply
aloud. The lamp example uses Iris's [home automation setup](home-automation.md).

First, open Iris's HTTPS chat page in Safari on your home Wi-Fi and make sure
you can send a message. If Safari shows a certificate warning with Caddy, go
back to the [certificate setup above](#enable-the-phone-microphone) and check
that the Caddy root's full trust switch is on, even if you installed it earlier.
You only need Safari for this check. The shortcut contacts Iris directly.

Open **Shortcuts** on the iPhone, tap **+**, and name the shortcut **Iris Relay**.
I use that name because Siri can hear “Ask Iris” as a question for Siri herself.
Add these actions in order.

1. Add **Ask for Input**, choose **Text**, and set the prompt to
   **“What do you want to ask Iris?”**
2. Add **Get Contents of URL**. Use the same HTTPS address and port as the chat
   page, replacing `/chat` with `/v1/text`. For example, use
   `https://192.168.1.10:8443/v1/text`. Expand the action, set **Method** to
   **POST**, and add these headers.

   | Header | Value |
   | --- | --- |
   | `Authorization` | `Bearer YOUR_CONNECTION_KEY` |
   | `Content-Type` | `text/plain` |
   | `X-Iris-Session` | A UUID for this shortcut |

   Use your `IRIS_DROID_TOKEN` in place of `YOUR_CONNECTION_KEY`, with `Bearer`
   spelled exactly as shown, followed by one space. To make a session ID, run
   `python -c "import uuid; print(uuid.uuid4())"` on your PC and paste the result
   into the header. Iris uses that ID to keep track of follow-up messages from
   this shortcut. You can keep using the same ID each time.

   Set **Request Body** to **File** and select the **Provided Input** magic
   variable from **Ask for Input** as the file. This sends the words you entered
   as the request body. [Apple's Shortcuts API guide](https://support.apple.com/guide/shortcuts/apd58d46713f/ios).
3. Add **Get Dictionary Value**. Set the key to `text` and use **Contents of URL**
   as its input. This picks Iris's reply out of the response.
4. Add **Speak Text** and select **Dictionary Value** as the text to read aloud.

Try the ▶ button in Shortcuts first. Type something like **“Is the living room
lamp on?”** and allow the connection if the phone asks. Once Iris replies, try
**“Hey Siri, Iris Relay”** and give your request after the prompt.
[Apple's guide to running shortcuts with Siri](https://support.apple.com/guide/shortcuts/apd07c25bb38/ios).

If the shortcut goes quiet after you enter a message, put a temporary **Quick
Look** action after **Get Contents of URL** and feed it **Contents of URL** so
you can see the server's reply. If it says **Authentication required**, check
the key in the web chat, make sure autocorrect kept the **z** in `Authorization`,
and check the capitalisation of `Bearer`.

[Back to Iris](../README.md) · [All guides](index.md)

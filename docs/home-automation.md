# The living room lamp

Iris can switch a Matter plug on and off, check its state, read its meters and
edit a weekly schedule. The small home automation service runs separately from
Iris. I run the voice client on the Pi too, but the two processes don't depend
on each other. Put the home service on whichever computer stays on and can
reach the plug on your local network. The PC can sleep and the schedule will
still run.

## Pair the plug with the Pi

You'll need 64-bit Raspberry Pi OS, Python 3.11 or newer, and a recent
[`chip-tool` build](https://project-chip.github.io/connectedhomeip-doc/development_controllers/chip-tool/chip_tool_guide.html)
with the Matter electrical measurement clusters. The home service invokes
that tool when it has something to do. You can build it on another machine
for the Pi's architecture. The Pi and plug need local IPv6 and multicast
discovery, so put them on the same LAN.

Create a directory for the controller's pairing keys, using the same Pi
account that will run the service.

```sh
install -d -m 700 ~/.config/iris ~/.local/state/iris-home ~/.local/state/iris-home/matter
```

Use the CHIP Tool guide to pair the plug over Bluetooth and Wi-Fi, or to add
it over the network if it is already on Wi-Fi and has an open pairing window.
Use `--storage-directory "$HOME/.local/state/iris-home/matter"` for every
command and choose a node ID, such as `91`. If the controller's trust store
doesn't recognise the plug's attestation certificate, add the appropriate
production PAA certificate to the trust store and supply
`--paa-trust-store-path` when pairing.

Read the node's endpoints and then the clusters on the endpoint you want to
use. For example, with node 91 and endpoint 1:

```sh
chip-tool descriptor read parts-list 91 0 --storage-directory "$HOME/.local/state/iris-home/matter"
chip-tool descriptor read server-list 91 1 --storage-directory "$HOME/.local/state/iris-home/matter"
chip-tool onoff read on-off 91 1 --storage-directory "$HOME/.local/state/iris-home/matter"
```

The switch endpoint needs On/Off (cluster 6). Power, voltage and current use
Electrical Power Measurement (144), and cumulative imported energy uses
Electrical Energy Measurement (145). Set each meter endpoint according to the
cluster list and leave it out if the plug doesn't report it. For the UK P110M
V1, [TP-Link added Matter energy monitoring in firmware 1.3.2](https://www.tp-link.com/uk/support/download/tapo-p110m/).
Individual attributes can still be unavailable on a particular unit. Keep the
pairing directory between restarts and stop the home service before using that
same directory with `chip-tool` by hand.

## Set up the home service

From the Iris checkout on the Pi, install the project in a Python environment if
you haven't already. The supplied unit assumes `~/iris-llm/.venv`. If this
machine is only running the home service, a virtual environment with Pydantic
and tzdata is sufficient for running `python -m iris_home.service` from the
checkout.

Save the following as `~/.config/iris/home.json`, with absolute paths for your
account. These example endpoints assume all three clusters are on endpoint 1.

```json
{
    "listen": "192.168.1.40",
    "port": 8766,
    "tls_cert": "/home/pi/.config/iris/home.crt",
    "tls_key": "/home/pi/.config/iris/home.key",
    "timezone": "Europe/London",
    "clock_sync_file": "/run/systemd/timesync/synchronized",
    "database": "/home/pi/.local/state/iris-home/schedules.sqlite3",
    "chip_tool": "/usr/local/bin/chip-tool",
    "matter_storage": "/home/pi/.local/state/iris-home/matter",
    "devices": {
        "living room lamp": {
            "protocol": "matter",
            "node": 91,
            "switch_endpoint": 1,
            "power_endpoint": 1,
            "energy_endpoint": 1
        }
    }
}
```

Use the Pi's LAN address in `listen`. A DHCP reservation keeps that address
stable. The example clock file is for Raspberry Pi OS using
`systemd-timesyncd`. It can sync from a time server on your LAN if you keep
the Pi off the internet. If you already ensure that the clock is correct
before starting the service by another means, omit `clock_sync_file`.

For LAN access the service requires TLS. If you don't already have a
certificate, generate one on the Pi with the address you put in `listen`.
Copy **only** `home.crt` to the PC over a trusted connection. Iris uses it to
check that it is talking to your Pi.

```sh
umask 077
cd ~/.config/iris
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
  -subj '/CN=Iris home service' -addext 'subjectAltName=IP:192.168.1.40' \
  -keyout home.key -out home.crt
printf 'IRIS_HOME_TOKEN=%s\n' "$(openssl rand -hex 32)" > home.env
```

Keep that token private and use the same value for `IRIS_HOME_SERVICE_TOKEN`
on the Iris server. Every request needs it. There's no need to forward the
service port at the router or use an SSH tunnel with this setup. If the home
service and Iris server are on the same machine, you can listen on
`127.0.0.1` and omit the certificate and key.

From the checkout, install the user service and enable it at boot:

```sh
mkdir -p ~/.config/systemd/user
cp systemd/iris-home.service ~/.config/systemd/user/
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now iris-home.service
journalctl --user -u iris-home.service -f
```

If your checkout or virtual environment is in a different place, edit the
installed unit's `WorkingDirectory` and `ExecStart`. The Iris auto-updater
restarts the home service after an update if it's running or failed. If you're
running the home service without the voice client, you can set just
`--service iris-home.service` in your installed updater unit.

## Let Iris use the home service

On the PC that runs the Iris server, add these to `local-config.json`:

```json
{
    "home_service_url": "https://192.168.1.40:8766",
    "home_service_ca": "C:/path/to/home.crt"
}
```

Set `IRIS_HOME_SERVICE_TOKEN` in the Iris server's environment to the token
from `home.env` and restart the Iris server. Keep `tools_enabled` on. Try
“Turn on the living room lamp”, “How many watts is the lamp drawing?” or
“Turn the lamp on at six and off at eleven every weekday”.

Schedules belong to the home service and use its configured timezone. If the
Pi missed several changes while it was off, it applies the latest missed
change after it comes back and can reach the plug. A successful manual
switch command takes precedence over older pending changes. A time skipped
by the clocks going forward is skipped by the schedule too. When the clocks
go back, a rule in the repeated hour runs the first time. The plug's own
Tapo schedules are separate, so clear any rules in the Tapo app that would
conflict with the ones you ask Iris to run.

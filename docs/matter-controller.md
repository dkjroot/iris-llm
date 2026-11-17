# Prepare the Matter controller on the Pi

This optional chapter is part of [setup section G](setup-devices.md#g-add-a-matter-over-wi-fi-plug).
It gets one Matter over Wi-Fi switch paired and working before Iris is involved.
An already working controller does not need rebuilding: keep its version and
pairing storage, check the commands in step 4, then return to section G.

The commands below run on the **64-bit Bookworm Pi**, in an SSH terminal. They
use the Pi's normal account, not root, except for installing OS packages and the
finished executable. The proposed source version is Matter SDK **v1.6.1.0**,
an [upstream release](https://github.com/project-chip/connectedhomeip/releases/tag/v1.6.1.0).
This build/firmware combination still needs a physical acceptance run. A version
number is not evidence that your particular plug has passed it.

## 1. Build chip-tool

`chip-tool` is the program Iris's home service uses to talk Matter. The source
build is large and can take hours on a Pi. Use an SSD or a large spare card with
plenty of free space; allow tens of gigabytes for the SDK and build, in addition
to Iris. `df -h "$HOME"` shows the free space. Don't fill the card holding your
only working home-controller state.

Open a fresh SSH terminal so the Iris virtual environment is not active:

```sh
sudo apt update
sudo apt install git gcc g++ pkg-config cmake curl libssl-dev libdbus-1-dev libglib2.0-dev libavahi-client-dev ninja-build python3-venv python3-dev python3-pip unzip libgirepository1.0-dev libcairo2-dev libreadline-dev libevent-dev default-jre
cd "$HOME"
git clone --depth 1 --branch v1.6.1.0 https://github.com/project-chip/connectedhomeip.git matter-sdk
cd matter-sdk
python3 scripts/checkout_submodules.py --shallow --platform linux
source scripts/activate.sh
scripts/examples/gn_build_example.sh examples/chip-tool out/iris-chip-tool
sudo install -m 0755 out/iris-chip-tool/chip-tool /usr/local/bin/chip-tool
git rev-parse HEAD
```

Record that revision and the final build result. Stop at any failed command;
don't install an old leftover binary after a build failure. The
[SDK build instructions](https://github.com/project-chip/connectedhomeip/blob/v1.6.1.0/docs/guides/BUILDING.md)
explain the environment/dependencies, and the
[controller instructions](https://project-chip.github.io/connectedhomeip-doc/development_controllers/chip-tool/chip_tool_guide.html)
describe its standalone build. Reopen a fresh SSH terminal afterwards to leave
the SDK's Python environment behind. Running `/usr/local/bin/chip-tool` should
print the available commands.

## 2. Keep a permanent pairing directory

Pairing gives this controller its own credentials for the plug. A temporary
directory would lose that identity on reboot. Use the same directory and account
for every command and, later, the home service:

```sh
install -d -m 700 "$HOME/.local/state/iris-home/matter"
```

The SDK includes [production attestation roots](https://github.com/project-chip/connectedhomeip/tree/master/credentials/production/paa-root-certs).
These let the controller verify the device's manufacturer credentials; they are
different from Iris's HTTPS certificates. Check the directory exists in your
checkout with `ls "$HOME/matter-sdk/credentials/production/paa-root-certs"`.
If your device needs a newer root, obtain it through the vendor/CSA production
trust route. Do not bypass attestation to make an error disappear.

## 3. Pair one device

Give your first device a controller node number, for example `91`. Use a
different unused number for each additional device. The commands below are
alternatives: choose the one matching the device's current state.

**Already on Wi-Fi:** in the app/controller that currently owns the plug, open
its Matter sharing/pairing window. This normally generates a new pairing code;
the exact menu belongs to that vendor. Replace `PAIRING_CODE` below with the
current manual code or QR payload. Keep the phone app's pairing window open:

```sh
chip-tool pairing code 91 'PAIRING_CODE' --storage-directory "$HOME/.local/state/iris-home/matter" --paa-trust-store-path "$HOME/matter-sdk/credentials/production/paa-root-certs"
```

**New Wi-Fi device:** put the plug into its manufacturer's pairing mode and keep
it near the Pi for Bluetooth commissioning. Read its setup code and your home
Wi-Fi credentials into shell variables so the literal values aren't entered as
commands in shell history:

```sh
read -r -p 'Wi-Fi name: ' iris_wifi_name
read -r -s -p 'Wi-Fi password: ' iris_wifi_password
printf '\n'
read -r -s -p 'Device pairing code: ' iris_pairing_code
printf '\n'
chip-tool pairing code-wifi 91 "$iris_wifi_name" "$iris_wifi_password" "$iris_pairing_code" --storage-directory "$HOME/.local/state/iris-home/matter" --paa-trust-store-path "$HOME/matter-sdk/credentials/production/paa-root-certs"
unset iris_wifi_name iris_wifi_password iris_pairing_code
```

The tool still receives credentials as process arguments; do this on your trusted
Pi account and keep commissioning logs private. Pairing should complete without
an attestation or timeout error. Discovery requires local IPv6 and multicast;
guest Wi-Fi/client isolation can stop it. A Thread-only device needs a different
network setup and is outside this Wi-Fi route. Do not factory-reset an existing
working device merely because the first attempt fails; first check its pairing
window, network and logs.

## 4. Discover the endpoint and prove control

A node can contain several endpoints, rather like numbered sockets for different
functions. First list them, then inspect the one offering the On/Off cluster:

```sh
chip-tool descriptor read parts-list 91 0 --storage-directory "$HOME/.local/state/iris-home/matter"
chip-tool descriptor read server-list 91 1 --storage-directory "$HOME/.local/state/iris-home/matter"
chip-tool onoff read on-off 91 1 --storage-directory "$HOME/.local/state/iris-home/matter"
chip-tool onoff on 91 1 --storage-directory "$HOME/.local/state/iris-home/matter"
chip-tool onoff read on-off 91 1 --storage-directory "$HOME/.local/state/iris-home/matter"
```

Replace endpoint `1` if discovery reports a different switch endpoint. Check the
lamp physically turns on and the final read reports on; repeat with `onoff off`
and another read. Cluster `6` is On/Off. Optional power/voltage/current readings
need cluster `144`; energy needs `145`. They may be on different endpoints or
absent. Record only capabilities the plug actually reports. Keep its model and
firmware version with your acceptance results.

Now return to [section G, step 2](setup-devices.md#g-add-a-matter-over-wi-fi-plug)
to put these identities into the home service configuration. Once the service
is running, stop it before manual commands that share its pairing storage.

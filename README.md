# SmartMicrophone

SmartMicrophone is a modern, open-source web-based microphone and remote control system for UltraStar Deluxe (USDX) and compatible karaoke games. It lets you use your smartphone as a wireless microphone, control the game from your phone, manage songs, and adjust settings---all through a fast, mobile-friendly web interface.

---

## Features
- **Wireless Microphone:** Use your phone as a microphone for UltraStar Deluxe.
- **Multi-mic Support:** Up to 6 virtual microphones, each mapped to a player slot.
- **Remote Control:** Send keystrokes and text to the game, including navigation and search.
- **Song Management:** Search, preview, and add songs to playlists.
- **Settings Panel:** Configure audio options, delays, and more --- instantly, and per device
- **Hotspot & Network Integration:** Supports Wi-Fi hotspot mode and advanced network forwarding.
- **Automatic Device Mapping:** Maps domain names to hotspot IPs for easy connection.
- **Secure HTTPS Option:** SSL support and port remapping for secure connections.

---

## Installation
git clone https://github.com/dgruss/SmartMicrophone.git
### Supported OS: Ubuntu/Debian (recommended)

#### 1. Install System Dependencies
```sh
sudo add-apt-repository ppa:longsleep/golang-backports
sudo apt update
sudo apt update
sudo apt install python3 python3-pip git make gcc make pkg-config libopus-dev libopusfile-dev libpulse-dev golang-go libsdl2-image-dev python3-flask pipewire pipewire-pulse
```

#### 2. Clone the Repository, Submodules, and build webrtc-cli
```sh
git clone https://github.com/dgruss/SmartMicrophone.git
cd SmartMicrophone
git submodule init
git submodule update
cd webrtc-cli
make
cd ..
```

#### 3. Prepare UltraStar Deluxe
- Install UltraStar Deluxe and place your songs in the appropriate folder (currently only the /songs folder is supported but symlinks are followed)
- Some features require using [the dgruss beta3](https://github.com/dgruss/USDX/tree/beta3) version as they are not upstreamed yet
- Make sure you know the path to your usdx directory (e.g., `/home/user/usdx`)

#### 4. (Optional) Configure SSL, Set up Wi-Fi Hotspot and Internet Forwarding
- Place your SSL certificate and key files in the project directory if you want HTTPS --- this might be required to convince your phone to use WebRTC. Self-signed certificates work. Otherwise you can use a domain or subdomain you have on the Internet (give the DNS entry a short lifetime on your server!) and configure SmartMicrophone to use domain and certificates
- See Advanced Networking below for details

## Usage

### Basic Server Startup
```sh
python3 server.py
```

Do **not** run the server with `sudo`.

### Common Command-Line Arguments

Some options perform operations that require `sudo` permission. However, SmartMicrophone should not be run with `sudo`. Instead, SmartMicrophone will invoke `sudo` internally, which means you may be prompted for your `sudo` password.


#### Networking & Security
| Option | Description |
|--------|-------------|
| `--start-hotspot <name>` | Start the given hotspot using nmcli before domain setup |
| `--internet-device <iface>` | Network interface providing internet connectivity (e.g., wlan0), invokes sudo |
| `--hotspot-device <iface>` | Network interface for the hotspot (e.g., wlan1), invokes sudo |
| `--ssl` | Enable SSL (requires --chain and --key) |
| `--chain <cert>` | SSL chain/cert file (fullchain.pem or cert.pem) |
| `--key <key>` | SSL private key file (privkey.pem) |
| `--port <port>` | Port to run the server on (default: 5000) |
| `--remap-ssl-port` | Remap ports so that users can access the server on the default HTTPS port, invokes sudo |
| `--domain <domain>` | Setup a domain to hotspot IP mapping via NetworkManager/dnsmasq, invokes sudo |

#### UltraStar Deluxe Integration
| Option | Description |
|--------|-------------|
| `--usdx-dir <path>` | Path to usdx directory (default: ../usdx) |
| `--playlist-name <name>` | Playlist filename (default: SmartMicSession.upl) |
| `--run-usdx` | Run UltraStar Deluxe after server startup |
| `--audio-format <ext>` | Audio format of songs in UltraStar Deluxe (default: m4a) |
| `--set-inputs` | Initialize [Record] section in config.ini for 6 virtual sinks |

#### Server Options
| Option | Description |
|--------|-------------|
| `--debug` | Enable debug mode |

### Example: Full Setup with Hotspot and Forwarding
```sh
python3 server.py --ssl --chain ../cert.pem --key ../key.pem --remap-ssl-port --domain usdx.gruss.cc --set-inputs --start-hotspot usdx --internet-device wlan1 --hotspot-device wlan0 --run-usdx
```

---

## Web Interface Overview

Access the server from your phone or computer:
```
http://<server-ip>:<port>/
```
Or, if using SSL and port remapping:
```
https://<domain>/
```

### Tabs & Screenshots
| &nbsp;                                                                 | &nbsp;                                                                                                   |
|----------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------
| <img src="static/microphone_tab.jpg" alt="Microphone Tab" width="90%"> | <img src="static/songs_tab.jpg" alt="Songs Tab" width="90%"> |
| **Microphone** <br>- View and join available mic slots<br>- Assign multiple phones to a mic<br>- See current assignments | **Songs** <br>- Search by artist or title<br>- Preview audio<br>- Add songs to playlist |
| <img src="static/control_tab.jpg" alt="Control Tab" width="90%"> | <img src="static/settings_tab.jpg" alt="Settings Tab" width="90%"> |
| **Control** <br>- Acquire/release control<br>- Send keystrokes<br>- Type text to the game<br>- Only one user controls at a time | **Settings** <br>- Noise suppression, echo cancellation, normalization<br>- Set per-player delay<br>- Change display name<br>- Enable debug output |
| <img src="static/lock_screen.jpg" alt="Lock Screen" width="90%"> | **Lock Screen** <br>- Prevent accidental taps/swipes<br>- Standby mode during singing |

## Advanced Networking

### Hotspot & Domain Mapping
- Use `--start-hotspot` to activate a Wi-Fi hotspot
- Use `--domain` to map a domain name to the hotspot IP for easy phone connection

### Internet Forwarding
- Use `--internet-device` and `--hotspot-device` to forward internet from one interface to another (iptables rules, requires sudo)

### SSL & Port Remapping
- Use `--ssl`, `--chain`, and `--key` for HTTPS
- Use `--remap-ssl-port` to allow access via port 443 (requires sudo)

---

## Troubleshooting

### Common Issues

- **Network problems:**
  - Verify that the hotspot exists in network manager with the corresponding name (e.g. usdx).
  - flush all iptables rules `sudo iptables -F`
  - First connect the internet device, then create the hotspot, then run this tool.

---

## Contributing
Pull requests and issues are welcome!
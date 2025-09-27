# pulse-receive

`pulse-receive` is a simple command-line application that demonstrates how to receive audio media using [Pion WebRTC](https://github.com/pion/webrtc) and play it live using PulseAudio. This is useful for redirecting WebRTC audio streams into other applications on your system.

Based on the [Pion gstreamer-receive example](https://github.com/pion/example-webrtc-applications/tree/master/gstreamer-receive) and [webrtc-cli](https://github.com/gavv/webrtc-cli).

**Supported OS:** Debian/Ubuntu only (Linux, PulseAudio required)

## Prerequisites

- Go 1.25 or newer
- Debian/Ubuntu Linux
- PulseAudio server running

## Install Dependencies

Install the required development headers and libraries:

```bash
sudo apt-get update
sudo apt-get install libpulse-dev libopus-dev pkg-config
```

## Build

Clone the repository and build the application:

```bash
git clone https://github.com/jonasjuffinger/webrtc-cli.git
cd webrtc-cli/pulse-receive
go build -o pulse-receive
```

## Usage


Expects the client's WebRTC SDP offer base64 encoded on stdin. The application will output its own SDP answer, also base64 encoded, to forward to the client.

```bash
echo "<base64-encoded-offer>" | ./pulse-receive [options] > answer.sdp
```

### Options

| Option            | Type    | Default     | Description                                                        |
|-------------------|---------|-------------|--------------------------------------------------------------------|
| --rate            | uint    | 48000       | Sample rate                                                        |
| --chans           | uint    | 2           | Number of channels                                                 |
| --pulse-buf       | duration| 20ms        | PulseAudio buffer size (lower for lower latency)                   |
| --debug           |         | false       | Enable more logs (minimal output otherwise)                        |

All durations can be specified as e.g. `40ms`, `1s`, etc.

## License

MIT (see [LICENSE](../LICENSE))

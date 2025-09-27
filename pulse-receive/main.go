// SPDX-FileCopyrightText: 2023 The Pion community <https://pion.ly>
// SPDX-License-Identifier: MIT

//go:build !js
// +build !js

// simple app to receive audio using Pion WebRTC and play using PulseAudio for redirection into other apps
package main

import (
		"bufio"
		"encoding/base64"
		"encoding/json"
		"errors"
		"flag"
		"fmt"
		"io"
		"os"
		"strings"
		"time"

	"github.com/pion/webrtc/v4"
	opus "gopkg.in/hraban/opus.v2"
	"github.com/dgruss/SmartMicrophone/pulse-receive/snd"
)

func main() {
		// Command-line flags
		rate := flag.Uint("rate", 48000, "sample rate")
		chans := flag.Uint("chans", 2, "number of channels")
		pulseBuf := flag.Duration("pulse-buf", 20*time.Millisecond, "PulseAudio buffer size")
		debug := flag.Bool("debug", false, "enable more logs (and minimal output otherwise)")

		flag.Parse()

		if *debug {
			fmt.Printf("Flags: rate=%d chans=%d pulse-buf=%s\n",
				*rate, *chans, pulseBuf.String())
		}

		// Prepare the configuration
		config := webrtc.Configuration{
			ICEServers: []webrtc.ICEServer{{URLs: []string{"stun:stun.l.google.com:19302"}}},
		}

	// Create a new RTCPeerConnection
	peerConnection, err := webrtc.NewPeerConnection(config)
	if err != nil {
		panic(err)
	}

		// Set a handler for when a new remote track starts and route supported media
		peerConnection.OnTrack(func(track *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
			mimeParts := strings.Split(track.Codec().RTPCodecCapability.MimeType, "/")
			codecName := mimeParts[len(mimeParts)-1]
			if *debug {
				fmt.Printf("Track has started, of type %d: %s \n", track.PayloadType(), codecName)
			}

			if strings.EqualFold(codecName, "rtx") {
				if *debug {
					fmt.Println("Ignoring RTX track")
				}
				return
			}

			switch track.Kind() {
			case webrtc.RTPCodecTypeAudio:
				if !strings.EqualFold(codecName, "opus") {
					if *debug {
						fmt.Printf("Audio codec %s not supported, ignoring\n", codecName)
					}
					return
				}
				go handleAudioTrack(track, *rate, *chans, *pulseBuf, *debug)
			case webrtc.RTPCodecTypeVideo:
				if *debug {
					fmt.Println("Ignoring video track, Audio only")
				}
			default:
				if *debug {
					fmt.Printf("Unsupported track type %s, ignoring\n", track.Kind())
				}
			}
		})

	// Set the handler for ICE connection state
	// This will notify you when the peer has connected/disconnected
	peerConnection.OnICEConnectionStateChange(func(connectionState webrtc.ICEConnectionState) {
		fmt.Printf("Connection State has changed %s \n", connectionState.String())
	})

	// Wait for the offer to be pasted
	offer := webrtc.SessionDescription{}
	decode(readUntilNewline(), &offer)

	// Set the remote SessionDescription
	err = peerConnection.SetRemoteDescription(offer)
	if err != nil {
		panic(err)
	}

	// Create an answer
	answer, err := peerConnection.CreateAnswer(nil)
	if err != nil {
		panic(err)
	}

	// Create channel that is blocked until ICE Gathering is complete
	gatherComplete := webrtc.GatheringCompletePromise(peerConnection)

	// Sets the LocalDescription, and starts our UDP listeners
	err = peerConnection.SetLocalDescription(answer)
	if err != nil {
		panic(err)
	}

	// Block until ICE Gathering is complete, disabling trickle ICE
	// we do this because we only can exchange one signaling message
	// in a production application you should exchange ICE Candidates via OnICECandidate
	<-gatherComplete

	// Output the answer in base64 so we can paste it in browser
	fmt.Println(encode(peerConnection.LocalDescription()))

	// Block forever
	select {}
}

func handleAudioTrack(track *webrtc.TrackRemote, rate uint, chans uint, pulseBuf time.Duration, debug bool) {
	const maxOpusFrameDuration = 120 * time.Millisecond
	frameDuration := pulseBuf

	if err := snd.SetPulseBufferSize(frameDuration); err != nil {
		fmt.Printf("unable to set pulse buffer size: %v\n", err)
	}

	channels := int(chans)
	sampleRate := int(rate)

	if track.Codec().Channels > 0 {
		channels = int(track.Codec().Channels)
	}
	if track.Codec().ClockRate > 0 {
		sampleRate = int(track.Codec().ClockRate)
	}

	player, err := snd.NewPulsePlayer(snd.Params{
		Rate:        sampleRate,
		Channels:    channels,
		FrameLength: frameDuration,
	})
	if err != nil {
		fmt.Printf("failed to create pulse player: %v\n", err)
		return
	}
	defer player.Stop()

	decoder, err := opus.NewDecoder(sampleRate, channels)
	if err != nil {
		fmt.Printf("failed to create opus decoder: %v\n", err)
		return
	}

	maxSamples := sampleRate * int(maxOpusFrameDuration/time.Millisecond) / 1000
	if maxSamples == 0 {
		maxSamples = sampleRate / 50
	}
	pcmBuffer := make([]int16, maxSamples*channels)
	playerErrs := player.Errors()

	for {
		packet, _, readErr := track.ReadRTP()
		if readErr != nil {
			if !errors.Is(readErr, io.EOF) {
				fmt.Printf("failed to read RTP packet: %v\n", readErr)
			}
			return
		}

		sampleCount, decodeErr := decoder.Decode(packet.Payload, pcmBuffer)
		if decodeErr != nil {
			fmt.Printf("failed to decode opus payload: %v\n", decodeErr)
			continue
		}

		pcm := make([]int16, sampleCount*channels)
		copy(pcm, pcmBuffer[:sampleCount*channels])

		select {
		case player.Batches() <- pcm:
		case err, ok := <-playerErrs:
			if ok && err != nil {
				fmt.Printf("pulseaudio playback error: %v\n", err)
			}
			return
		}
	}
}

// Read from stdin until we get a newline.
func readUntilNewline() (in string) {
	var err error

	r := bufio.NewReader(os.Stdin)
	for {
		in, err = r.ReadString('\n')
		if err != nil && !errors.Is(err, io.EOF) {
			panic(err)
		}

		if in = strings.TrimSpace(in); len(in) > 0 {
			break
		}
	}

	fmt.Println("")

	return
}

// JSON encode + base64 a SessionDescription.
func encode(obj *webrtc.SessionDescription) string {
	b, err := json.Marshal(obj)
	if err != nil {
		panic(err)
	}

	return base64.StdEncoding.EncodeToString(b)
}

// Decode a base64 and unmarshal JSON into a SessionDescription.
func decode(in string, obj *webrtc.SessionDescription) {
	b, err := base64.StdEncoding.DecodeString(in)
	if err != nil {
		panic(err)
	}

	if err = json.Unmarshal(b, obj); err != nil {
		panic(err)
	}
}

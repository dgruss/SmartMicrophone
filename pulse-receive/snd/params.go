package snd

import "time"

type Params struct {
	DeviceOrFile string
	Rate         int
	Channels     int
	FrameLength  time.Duration
	LinkName     string
}

package snd

import (
	"bytes"
	"encoding/binary"
)

func int16ToBytes(i []int16) []byte {
	var b bytes.Buffer
	b.Grow(len(i) * 2)

	if err := binary.Write(&b, binary.LittleEndian, i); err != nil {
		panic(err)
	}

	return b.Bytes()
}

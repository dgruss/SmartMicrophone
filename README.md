# Virtual Microphone using WebRTC
A webserver and site that allows you to use your smartphone as a microphone with as little latency as possible.
It supports multiple phones connecting, each creating a microphone on the host computer.

## Status
This is not even a alpha version yet...


## Notes
General notes during development

#### Virtual Mic
Use these commands to create a virtual speaker and connect it to a virtual mic:
```
pactl load-module module-null-sink sink_name="dummy-mic1"
pactl load-module module-remap-source master=virt-mic-1-sink.monitor source_name=virt-mic-1 source_properties=device.description=virt-mic-1
```

#### Captive Portals
Information on captive portals:
https://gist.github.com/theprojectsomething/a8406ba6be3ed3335fb3a2e5efea4b41
https://unix.stackexchange.com/questions/386242/implementing-a-captive-portal-using-apache
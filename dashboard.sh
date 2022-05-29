#!/bin/sh
xset -dpms        # Disable DPMS (Energy Star) features.
xset s off        # Disable screen saver
xset s noblank    # Do not blank the video device
#unclutter &       # Hide X mouse cursor unless mouse activated


while true; do
	python /home/pi/dashboard/main.py
	sleep 1
done

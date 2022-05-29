from kivy.core.window import Window
from kivy.app import App
from kivy.clock import Clock
from kivy.properties import BoundedNumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.bubble import Bubble
from kivy.uix.label import Label
from kivy.graphics import Rotate
from kivy.graphics.context_instructions import PushMatrix, PopMatrix
from kivy_garden.mapview import MapView, MarkerMapLayer, MapMarker, MapMarkerPopup
from datetime import datetime
from time import sleep
from math import sqrt
from queue import Queue
from threading import Thread
import requests


LATITUDE      = 0.0
LONGITUDE     = 0.0
ZOOM_LEVEL    = 7
DUMP1090_HOST = 'dump1090server'


class AircraftMarker(MapMarker):
	angle = BoundedNumericProperty(0, min=0, max=359)

	def __init__(self, **kwargs):
		super(AircraftMarker, self).__init__(**kwargs)

		# Apply rotation to this widget only
		with self.canvas.before:
			PushMatrix()
			self.rotate = Rotate()

		with self.canvas.after:
			PopMatrix()

		self.bind(pos=self.update_canvas)
		self.bind(size=self.update_canvas)
		self.bind(angle=self.update_canvas)

	def update_canvas(self, *args):
		# Set new rotation origin on position change
		self.rotate.origin = self.center
		# Aircraft track equals inverted angle
		self.rotate.angle  = -self.angle

	def update_data(self, data):
		if('lat' in data and 'lon' in data):
			# Set new coordinates, real widget position is updated from this by map layer reposition()
			self.lat = data['lat']
			self.lon = data['lon']

		if('track' in data):
			self.angle = data['track']

		# Change icon for un-/known position
		if('seen_pos' in data and data['seen_pos'] < 5):
			self.source = 'images/marker_arrow_blue.png'
		else:
			self.source = 'images/marker_arrow_grey.png'


class AircraftInfo(MapMarkerPopup):
	def __init__(self, **kwargs):
		super(AircraftInfo, self).__init__(**kwargs)
		self.is_open        = True
		self.popup_size     = (68, 38)

		self.flight_text    = ''
		self.squawk_text    = ''
		self.speed_text     = ''
		self.altitude_text  = ''
		self.vert_rate_text = ''

		bubble              = Bubble(orientation='vertical')
		self.label_line1    = Label(font_size=10)
		self.label_line2    = Label(font_size=10)
		self.label_line3    = Label(font_size=10)

		bubble.add_widget(self.label_line1)
		bubble.add_widget(self.label_line2)
		bubble.add_widget(self.label_line3)

		self.add_widget(bubble)

	def update_data(self, data):
		if('lat' in data and 'lon' in data):
			# Set new coordinates, real widget position is updated from this by map layer reposition()
			self.lat = data['lat']
			self.lon = data['lon']

		if('flight' in data):
			# Flight number may contain trailing spaces, so strip it
			self.flight_text = data['flight'].strip()

		if('speed' in data):
			# kt -> km/h
			self.speed_text = '{:.0f} kph'.format(data['speed'] * 1.852)

		if('squawk' in data):
			self.squawk_text = data['squawk']

		if('altitude' in data):
			# ft -> m
			self.altitude_text = '{:.0f} m'.format(data['altitude'] / 3.2808)

		if('vert_rate' in data):
			# ft/min -> m/min
			self.vert_rate_text = '{:+.0f}'.format(data['vert_rate'] / 3.2808)

		self.label_line1.text = self.flight_text
		self.label_line2.text = self.speed_text + ' ' + self.squawk_text
		self.label_line3.text = self.altitude_text + ' ' + self.vert_rate_text


class Map(MapView):
	CHECK_DATA_INTERVAL_SECONDS = 1

	def __init__(self, **kwargs):
		super(Map, self).__init__(**kwargs)
		self.lat  = LATITUDE
		self.lon  = LONGITUDE
		self.zoom = ZOOM_LEVEL

		# Add custom marker layers for trace markers and aircraft position/info (last on top)
		self.trace_layer    = MarkerMapLayer()
		self.aircraft_layer = MarkerMapLayer()
		self.add_layer(self.trace_layer)
		self.add_layer(self.aircraft_layer)

		# Add center marker to aircraft marker layer to always show it on top
		self.add_marker(MapMarker(lat=self.lat, lon=self.lon, source='images/marker_center.png'), self.aircraft_layer)

		# Create dict to store references for each aircraft
		self.aircrafts = {}

		# Setup result queue, start data request worker thread and check/process timer
		self.data_queue = Queue(maxsize=2)
		Thread(target=self.request_worker, daemon=True).start()
		Clock.schedule_interval(self.check_data, self.CHECK_DATA_INTERVAL_SECONDS)

	def request_worker(self):
		while True:
			try:
				response = requests.get('http://' + DUMP1090_HOST + '/dump1090/data/aircraft.json')
				self.data_queue.put(response.json())
			except:
				sleep(1)

	def check_data(self, *args):
		try:
			data = self.data_queue.get(block=False)
			self.process_data(data)
		except:
			pass

	def cleanup_data(self):
		# Clean up timed out aircrafts
		for key in list(self.aircrafts.keys()):
			marker, info, active, trace_markers = self.aircrafts[key]

			if(active):
				# Reset active flag to see if the aircraft gets updated
				self.aircrafts[key] = (marker, info, False, trace_markers)
			else:
				# Remove aircraft if the active flag was not set during data processing
				self.remove_marker(marker)
				self.remove_marker(info)
				for trace_marker in trace_markers:
					self.remove_marker(trace_marker)
				del self.aircrafts[key]

	def process_data(self, data):
		self.cleanup_data()

		for aircraft in data['aircraft']:
			marker        = None
			info          = None
			trace_markers = []

			if('hex' in aircraft):
				if(aircraft['hex'] in self.aircrafts):
					# Get existing data
					marker, info, active, trace_markers = self.aircrafts[aircraft['hex']]

				if('lat' in aircraft and 'lon' in aircraft):
					# Add a trace point marker for new aircrafts and if the distance to the last position of the existing one is far enough
					points = len(trace_markers)
					if(points < 1 or (sqrt((abs(trace_markers[points - 1].lat - aircraft['lat']) ** 2) + (abs(trace_markers[points - 1].lon - aircraft['lon']) ** 2)) > 0.00001)):
						trace_marker = MapMarker(lat=aircraft['lat'], lon=aircraft['lon'], source='images/marker_trace.png')
						self.add_marker(trace_marker, self.trace_layer)
						trace_markers.append(trace_marker)

					# Create and add position/info marker for a new aircraft
					if(not marker):
						marker = AircraftMarker(source='images/marker_arrow_blue.png')
						self.add_marker(marker, self.aircraft_layer)

					if(not info):
						info = AircraftInfo(source='images/marker_invisible.png')
						self.add_marker(info, self.aircraft_layer)

				# Feed probably updated data to the markers
				if(marker):
					marker.update_data(aircraft)

				if(info):
					info.update_data(aircraft)

				# Write back data to aircraft dict, marking the aircraft as active
				if(marker and info):
					self.aircrafts[aircraft['hex']] = (marker, info, True, trace_markers)

		# Trigger refresh of position/info markers on their layer
		self.aircraft_layer.reposition()


class Statusbar(BoxLayout):
	def __init__(self, **kwargs):
		super(Statusbar, self).__init__(**kwargs)
		self.orientation = 'horizontal'

		self.add_widget(Label(text='', size_hint=(.4, 1)))
		self.datetime_label = Label(text='Mo, 00.00.0000  00:00:00', size_hint=(.2, 1))
		self.add_widget(self.datetime_label)
		self.add_widget(Label(text='', size_hint=(.4, 1)))

		Clock.schedule_interval(self.update_datetime, 1)

	def update_datetime(self, *args):
		self.datetime_label.text = datetime.now().strftime('%a, %d.%m.%Y    %H:%M:%S')


class MainScreen(BoxLayout):
	def __init__(self, **kwargs):
		super(MainScreen, self).__init__(**kwargs)
		self.orientation = 'vertical'
		self.add_widget(Statusbar(size_hint=(1, .05)))
		self.add_widget(Map(size_hint=(1, .95)))


class DashboardApp(App):
	def build(self):
		return MainScreen()


if __name__ == '__main__':
	# Set fullscreen resolution, otherwise it is 800x600
	Window.size = (1360, 768)
	Window.fullscreen = True
	DashboardApp().run()

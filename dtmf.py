#!/usr/bin/python

# This file is part of asterisk-phonepatch

# Copyright (C) 2006 Arnau Sanchez
#
# Asterisk-phonepatch is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License or any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

# Standard Python modules
import os, sys, struct
import math, numarray, optparse, FFT

__version__ = "$Revision: 1.7 $"
__author__ = "Arnau Sanchez <arnau@ehas.org>"
__depends__ = ['FFT', 'Numeric-Extension', 'Python-2.4']
__copyright__ = """Copyright (C) 2006 Arnau Sanchez <arnau@ehas.org>.
This code is distributed under the terms of the GNU General Public License."""

### Global variables

# DTMF tones
DTMF_LOW_FREQS = (697.0, 770.0, 852.0, 941.0)
DTMF_HIGH_FREQS = (1209.0, 1336.0, 1477.0, 1633.0)
DTMF_FREQS = DTMF_LOW_FREQS + DTMF_HIGH_FREQS
DTMF_TABLE = ("123A", "456B", "789C", "*0#D")

# Not used by now
BUSY_FREQS = (480.0, 620.0)
DIALTONE_FREQS = (350.0, 440.0)

# Decoding constants
MIN_RATE = 8000
MIN_TONETIME = 0.05
SUBWINDOW = 2

DEF_MIN_F1_POWER = 0.05
DEF_DECODE_OVERPOWER = 20
DEF_MIN_DIFF23_POWER = 20
DEF_MAX_DIFF12_POWER = 10

PEAK_UP = 1
PEAK_DOWN = 1

# dictionary: string_format : bit_order, sample size, signed/unsigned
AFMT_TO_DEF = { "S8": "=bS", "U8": "=BU", "S16_LE": "<hS", "U16_LE": "<HU", "S16_BE": ">hS", "U16_BE": ">HU"}
	
### Global functions

#################################
def debug(args):
	sys.stderr.write(str(args) + "\n")
	sys.stderr.flush()

#################################
def key_to_freqs(key):
	"""Get pair freqs for a given DMTF key"""
	for row_index, row in enumerate(DTMF_TABLE):
		column_index = row.find(key)
		if column_index < 0: continue
		return DTMF_LOW_FREQS[row_index], DTMF_HIGH_FREQS[column_index]
	return None

#################################
def get_dtmf_keys():
	"""Get string with all DTMF keys"""
	return "".join(DTMF_TABLE)

################################
def freqs_to_key(freq1, freq2):
	"""Get DTMF key from pair of freqs (tuple or list)"""
	low_freq, high_freq = freq1, freq2
	if high_freq < low_freq: low_freq, high_freq = high_freq, low_freq
	if low_freq not in DTMF_LOW_FREQS or high_freq not in DTMF_HIGH_FREQS:
		return None
	low_index = list(DTMF_LOW_FREQS).index(low_freq)
	high_index = list(DTMF_HIGH_FREQS).index(high_freq)
	return DTMF_TABLE[low_index][high_index]

###################################################
###################################################
class Generator:
	"""Generate an audio with one or multiple DTMF tones"""
	################################################
	def __init__(self, **args):
		self.samplerate = args.get("samplerate", MIN_RATE)
		if self.samplerate < MIN_RATE:
			raise ValueError, "Samplerate must be equal or higher than %d" %MIN_RATE
		self.channels = args.get("channels", 1)
		self.buffersize = args.get("buffersize", 0)
		base_format = args.get("sampleformat", "S16_LE")
		format = base_format.upper()
		try: sampleformatdef = AFMT_TO_DEF[format]
		except: raise NameError, "sample audio format not supported: %s" %base_format
		self.samplebyteorder, self.samplectype, self.samplesign  = sampleformatdef
		self.float_to_audio = (1 << len(struct.pack(self.samplectype, 0)) * 8) / 2.0
		self.audio_offset = 0
		if self.samplesign == "U": self.audio_offset = 1.0

	################################################
	def encode_keys(self, keys, time, wait, gain = 1.0):
		for index, key in enumerate(keys):
			for buffer in self.encode_key(key, time, gain):
				yield buffer
			if index == len(keys) - 1: break			
			for buffer in self.silence(wait):
				yield buffer

	################################################
	def encode_key(self, key, time, gain = 1.0):
		if type(key) != str or len(key) != 1 or key not in get_dtmf_keys():
			raise NameError, "Unknown DTMF key: %s" %key
		low_freq, high_freq = key_to_freqs(key)
		index = 0
		c1 = 2 * math.pi * low_freq /  self.samplerate 
		c2 = 2 * math.pi * high_freq /  self.samplerate 
		nsamples_pending = int(time * self.samplerate)
				
		while nsamples_pending:
			size = self.buffersize or nsamples_pending
			nsamples = min(nsamples_pending, size)
			nsamples_pending -= nsamples
			v1 = numarray.array([ math.sin(c1 * (index+i)) for i in range(nsamples)])
			v2 = numarray.array([ math.sin(c2 * (index+i)) for i in range(nsamples)])
			index += nsamples
			output = gain * self.float_to_audio * (0.5 * v1 + 0.5 * v2 + self.audio_offset)
			if self.channels > 1:
				output = output.repeat(self.channels)
			format = self.samplebyteorder + str(nsamples*self.channels) + self.samplectype
			buffer = struct.pack(format, *output)			
			yield buffer

	################################################
	def silence(self, time):
		nsamples = int(time * self.samplerate) * self.channels
		format = self.samplebyteorder + self.samplectype
		buffer = struct.pack(format, self.audio_offset) * nsamples 
		size = self.buffersize or len(buffer)
		while buffer:
			yield buffer[:size]
			buffer = buffer[size:]

###################################################
###################################################
class Decoder_state:
	################################################
	def __init__(self, decoder):
		self.key_state = dict([(x, 0) for x in get_dtmf_keys()])
		self.current_key = None
		self.min_peaks = SUBWINDOW

###################################################
###################################################
class Decoder:
	################################################
	def __init__(self, **args):
		self.samplerate = args.get("samplerate", MIN_RATE)
		if self.samplerate < MIN_RATE:
			raise ValueError, "Samplerate must be equal or higher than %d" %MIN_RATE
		
		self.channels = args.get("channels", 1)
		self.verbose = args.get("verbose", 0)
		self.sensibility = args.get("sensibility", 1.0)
		
		# Decoding parameters
		self.min_f1_power = DEF_MIN_F1_POWER * self.sensibility
		self.decode_overpower = DEF_DECODE_OVERPOWER / self.sensibility
		self.max_diff12_power = DEF_MAX_DIFF12_POWER * self.sensibility
		self.min_diff23_power = DEF_MIN_DIFF23_POWER / self.sensibility
		
		base_format = args.get("sampleformat", "S16_LE")
		format = base_format.upper()
		try: sampleformatdef = AFMT_TO_DEF[format]
		except: raise ValueError, "Audio format not supported: %s" %base_format
		self.samplebyteorder, self.samplectype, self.samplesign  = sampleformatdef
		self.audio_to_float = 1.0 / float( (1 << len(struct.pack(self.samplectype, 0)) * 8) / 2.0 )
		self.sample_length = len(struct.pack(self.samplectype, 0))
		self.audio_offset = 0.0
		if self.samplesign == "U": 
			self.audio_offset = 1.0
		self.input = []
		d1 = [((f1, f2), freqs_to_key(f1, f2)) for f1 in DTMF_LOW_FREQS for f2 in DTMF_HIGH_FREQS]
		d2 = [((f2, f1), freqs_to_key(f1, f2)) for f1 in DTMF_LOW_FREQS for f2 in DTMF_HIGH_FREQS]
		self.freqs_to_key_dict = dict(d1 + d2)
		self.windowsize = int(MIN_TONETIME * self.samplerate / SUBWINDOW)
		self.ds = Decoder_state(self)
		
		self.cosarray = {}
		self.sinarray = {}
		for freq in DTMF_FREQS:
			self.sinarray[freq] = numarray.array([math.sin(2*math.pi*freq*x/self.samplerate) for x in range(0, self.windowsize)])
			self.cosarray[freq] = numarray.array([math.cos(2*math.pi*freq*x/self.samplerate) for x in range(0, self.windowsize)])

		self.debug("sampling rate: %d" %self.samplerate)
		self.debug("channels: %d" %self.channels)
		self.debug("window size: %d samples" %self.windowsize)
		self.debug("min peaks decoding: %d" %self.ds.min_peaks)
		self.debug("Freqs 1&2 overpower: %0.2f dB" %(10*math.log(self.decode_overpower, 10)))
		self.debug("Freqs 1&2 max diff: %0.2f dB" %(10*math.log(self.max_diff12_power, 10)))
		self.debug("Freqs 2&3 min diff: %0.2f dB" %(10*math.log(self.min_diff23_power, 10)))


	###############################
	def debug(self, log, level = 1):
		if self.verbose < level: return
		debug("decoder - " + log)

	#################################
	def decoding_simple(self, freq_power):		
		# Get 2 max frequencies
		sorted_freqs = [(freq_power[freq], freq) for freq in freq_power]
		sorted_freqs.sort()
		sorted_freqs.reverse()	
		f1power, f1 = sorted_freqs[0]
		f2power, f2 = sorted_freqs[1]
		f3power, f3 = sorted_freqs[2]
		f1realpower = (1000000 * f1power) / (self.windowsize)**2
		try: key_max = self.freqs_to_key_dict[(f1,f2)]
		except: key_max = None
		self.debug("f1=%0.2f (%0.5f), f2=%0.2f (%0.5f), f3=%0.2f (%0.5f)" %(f1, f1power, f2, f2power, f3, f3power), 2)
		if key_max:
			# Calculate mean power (discard the 2 max-frequencies) and min acceptable power
			mean_power = 0
			for power, freq in sorted_freqs[2:]:
				mean_power += power
			mean_power = mean_power / (len(DTMF_FREQS) - 2)
			min_overpower = mean_power * self.decode_overpower

			# Check if higher frequencies are greater than minimum acceptable power
			if f1realpower > self.min_f1_power and f2power > min_overpower and f1power < f2power * self.max_diff12_power and f2power > f3power * self.min_diff23_power:			
				key_max = self.freqs_to_key_dict[(f1,f2)]
				self.debug("key_max: %s. ** ok **" %key_max, 2)
				self.debug("f1power -- %f" %(f1realpower), 1)
				self.debug("f2power > min_overpower -- %f > %f" %(f2power, min_overpower), 1)
				self.debug("f1power < f2power * max_diff12 -- %f < %f" %(f1power, f2power * self.max_diff12_power), 1)
				self.debug("f2power > f3power * min_diff23 -- %f > %f" %(f2power, f3power * self.min_diff23_power), 1)
			else: 
				self.debug("key_max: %s. ko" %key_max, 2)
				key_max = None
				self.debug("f2power > min_overpower -- %f / %f" %(f2power, min_overpower), 2)
				self.debug("f1power < f2power * max_diff12 -- %f / %f" %(f1power, f2power * self.max_diff12_power), 2)
				self.debug("f2power > f3power * min_diff23 -- %f / %f" %(f2power, f3power * self.min_diff23_power), 2)

		output = []

		# Update state for each key. If min_peaks is reached for a key, append in DTMF output
		if key_max or self.ds.current_key:
			self.debug("%s-%s" %(key_max, self.ds.current_key))
		for key in get_dtmf_keys():
			old_state = self.ds.key_state[key]
			if key == key_max:
				if self.ds.key_state[key] < self.ds.min_peaks:
					self.ds.key_state[key] += PEAK_UP
				if self.ds.key_state[key] >= self.ds.min_peaks and self.ds.current_key != key:
					for k in [k for k in get_dtmf_keys() if k != key]:
						self.ds.key_state[k] = 0
					output.append(key)
					self.ds.current_key = key
					break
			elif key == self.ds.current_key and old_state > 0:
				self.debug("peak_down: %f" %self.ds.key_state[key])
				self.ds.key_state[key] -= PEAK_DOWN
				if self.ds.key_state[key] <= 0:
					self.ds.key_state[key] = 0
					self.ds.current_key = None
		return output

	################################################
	def decode_buffer(self, buffer):
		import audioop
		if self.channels == 2:
			buffer = audioop.tomono(buffer, self.sample_length, 0.5, 0.5)
		format = self.samplebyteorder + str(len(buffer) / self.sample_length) + self.samplectype
		abuffer = numarray.array(struct.unpack(format, buffer)) * self.audio_to_float
		if self.audio_offset:
			abuffer -= self.audio_offset
		self.input = self.input + abuffer.tolist()
		#self.input = numarray.concatenate([self.input, abuffer]).tolist()
		dtmf_output = []
		while len(self.input) >= self.windowsize:
			window = numarray.array(self.input[:self.windowsize])
			self.input = self.input[self.windowsize:]
			fft = {}
			for freq in DTMF_FREQS:
				fft[freq] = (((self.sinarray[freq] * window)).sum())**2 + (((self.cosarray[freq] * window)).sum())**2
			keys = self.decoding_simple(fft)
			dtmf_output += keys
		
		return dtmf_output

###########################
def main():
	usage = """
	dtmf.py [options]: DTMF generator/decoder
	
	You must activate a generator or decoding option"""
	
	parser = optparse.OptionParser(usage)
	
	parser.add_option('-v', '--verbose-level', default=0, dest='verbose', action="count", help='Increase verbose level')
	parser.add_option('-s', '--samplerate', dest='samplerate', default=8000, metavar='SPS', type='int', help = 'Set sampling rate')
	parser.add_option('-f', '--sampleformat', dest='sampleformat', default = "S16_LE", metavar='AFMT_FORMAT', type='string', help='Set audio sample format')
	parser.add_option('-c', '--channels', dest='channels', default=1, metavar='NUMBER', type='int', help = 'Set audio channels')
	parser.add_option('-e', '--sensibility', dest='sensibility', default=1.0, metavar='VALUE', type='float', help = 'Decoding sensibility (1.0 for normal)')
	parser.add_option('-g', '--generate', dest='generate', default = "", metavar='KEYS, TONETIME, WAITTIME, GAIN', type='string', help = 'DTMF generator')
	parser.add_option('-d', '--decode', dest='decode', default=False, action='store_true', help = 'DTMF decoder')
	parser.add_option('-b', '--buffersize', dest='buffersize', default=1024, metavar="BYTES", type='int', help='Buffer size for input/output')

	options, args = parser.parse_args()
	stdin, stdout = sys.stdin.fileno(), sys.stdout.fileno()
	if options.generate:
		keys, tonetime, waittime, gain = options.generate.split(",")
		enc = Generator(samplerate = options.samplerate, sampleformat = options.sampleformat, \
			channels = options.channels, buffersize = options.buffersize, verbose = options.verbose)
		for buffer in enc.encode_keys(keys, float(tonetime), float(waittime), float(gain)):
			os.write(stdout, buffer)
	
	elif options.decode:
		dec = Decoder(samplerate = options.samplerate, sampleformat = options.sampleformat, \
			channels = options.channels, sensibility = options.sensibility, verbose = options.verbose)
		while 1:
			buffer = os.read(stdin, options.buffersize)
			if not buffer: break		
			for key in dec.decode_buffer(buffer):
				os.write(stdout, key)
		os.write(stdout, "\n")
	else:
		parser.print_help()
		sys.exit(1)
	
	sys.exit(0)

############################
if __name__ == "__main__":
	main()

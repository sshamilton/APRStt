#!/usr/bin/python

# asterisk-phonepatch - Phonepatch for the Asterisk PBX

# Copyright (C) 2006 Arnau Sanchez
#
# This program is free software; you can redistribute it and/or
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
import sys, os, optparse, tempfile
import time, select, popen2, re
import signal, inspect, grp
import syslog, errno, pwd
import threading, audioop

# External phonepatch modules
sys.path.append("/usr/lib/asterisk-phonepatch")
import templateparser, dtmf, radio
import radiocontrol
import unixsocket
import daemonize

__version__ = "$Revision: 1.14 $"
__author__ = "Arnau Sanchez <arnau@ehas.org>"
__depends__ = ['Asterisk', 'Sox', 'Festival', 'Python-2.4']
__copyright__ = """Copyright (C) 2006 Arnau Sanchez <arnau@ehas.org>.
This code is distributed under the terms of the GNU General Public License."""

# CTCSS Private Lines Codes (used by Motorola)
PL_CODES = {"XZ": 67.0, "WZ": 69.3, "XA": 71.9, "WA": 74.4, "XB": 77.0, "SP": 79.7, 
	"YZ": 82.5, "YA": 85.4, "YB": 88.5, "ZZ": 91.5, "ZA": 94.8, "ZB": 97.4, "1Z": 100.0,
	"1A": 103.5, "1B" : 107.2, "2Z": 110.9, "2A": 114.8, "2B": 118.8, "3Z": 123.0,
	"3A": 127.3, "3B": 131.8, "4Z": 136.5, "4A": 141.3, "4B": 146.2, "5Z": 151.4,
	"5A": 156.7, "5B": 162.2, "6Z": 167.9, "6A": 173.8, "6B": 179.9, "7Z": 186.2,
	"7A": 192.8, "M1": 203.5, "8Z": 206.5, "M2" : 210.7, "M3": 218.1, "M4": 225.7,
	"9Z": 229.2, "M5": 233.6, "M6": 241.8, "M7": 250.3, "0Z": 254.1}

OUTCALL_IDNAME = "_phonepatch_"
GLOBAL_SECTION = "general"
PIDFILE_DIR = "/var/run/asterisk"
DEFAULT_SECTION = None

###############################
###############################
class Container:
	def __init__(self, **args):
		for arg in args:
			setattr(self, arg, args[arg])	

################
def run_command(command, input=None):
	popen = popen2.Popen3(command)
	if input: popen.tochild.write(input)
	popen.tochild.close()
	out = popen.fromchild.read()
	retval = popen.wait() >> 8
	return retval, out

###############################
###############################
class Phonepatch:
	"""A fully-configurable Radio-Phonepatch for the Asterisk PBX.
	
	The term "phonepatch" usually refers to the hardware device used to 
	connect a radio transceiver and a phoneline. This phonepatch takes 
	advantage of the <EAGI> Asterisk feature, and using <sox> as sound 
	converter and <festival> as text-to-speech syntetizer, provides a 
	powerful and fully configurable software-phonepatch. 
	
	It is only necessary to setup the hardware interface between computer and 
	radio, which involves audio (using the soundcard as D/A, A/D converter) 
	and PTT (Push-to-Talk). Please efer to Thomas Sailer's soundmodem project 
	for more information about that issue.

	This class has three possible modes, depending if it should run as an <AGI 
	incall>, <AGI outcall> or <Outcaller Daemon>, correspoding to incall(), 
	outcall() and daemon() methods. First, instance the <Phonepatch> class 
	and call one of these methods. Note that incall() and outcall() must be 
	run only as EAGI (Enhanced AGI) scripts (that's it, called from Asterisk),
	while the daemon() method should be run from command-line.	
	"""
	###################################
	def __init__(self, configuration, verbose=False):
		"""Configuration is a dictionary variable whose keys with sections:
		asterisk, soundcard, festival, telephony, dtmf, radio, incall and outcall
		"""
		self.configuration = configuration
		self.verbose = verbose
		self.modules_verbose = verbose
		self.set_state("phonepatch")
			
		self.phonepatch_extension = None
		self.phonepatch_phpconfig = None

		# Asterisk definitions
		self.pid = os.getpid()
		self.asterisk_fifo = os.path.join("/tmp/php_fifo_%d.raw"%self.pid)
		self.asterisk_samplerate = 8000
		self.asterisk_channels = 1
		
		# Audio data properties
		self.buffer_size = 1024
		self.sample_width = 2
		self.sample_max = 2.0**(self.sample_width*8) / 2.0
				
		# Parameters used to call sox later
		self.sox_pars = "-t alsa -c%d -s -e unsigned" %self.asterisk_channels
		
		# Festival (audio-to-speech generator) options
		#self.festival_isotolang = {}
		self.festival_isotolang = {"es": "spanish", "cy": "welsh"}
			
		# Configuration options
		self.festival_indicator = "@"
		self.last_playargs = None
		
		# Create signals dictionary: key = signal_int - value = signal_name
		self.signals = {}
		for var, value in inspect.getmembers(signal):
			if var.find("SIG") == 0 and var.find("SIG_") != 0: 
				self.signals[value] = var

		self.background = False
		self.ctcss_tx = None
		self.pidfile_created = False
		self.control_enabled = self.accept_agicalls = False
		self.call_active = None
		self.set_configuration_sections()

	####################################
	def signal_handler(self, signum, frame):
		"""Signal handler for:
		
		SIGTERM/SIGKILL: Kill phonepatch daemon()
		"""
		signame = self.signals.get(signum, "unknown")
		self.debug("signal_handler: received %s" %signame)
		
		if signum == signal.SIGTERM or signum == signal.SIGINT:
			self.debug("signal_handler: phonepatch daemon killed")
			self.end_daemon()
			os._exit(0)

	####################################
	def init_php(self, phonepatch):	
		# Initialize DTMF decoder
		if not phonepatch: 
			self.debug("init_php: phonepatch name not given")
			return
		if phonepatch not in self.configuration: 
			self.debug("init_php: phonepatch name not found in configuration: %s" %phonepatch)
			return
		self.phonepatch_phpconfig = phonepatch
		self.samplerate = self.asterisk_samplerate
		self.dtmf_decoder = dtmf.Decoder(samplerate = self.samplerate, \
			channels = self.asterisk_channels, \
			sensibility = self.getconf("dtmf_sensibility"), \
			verbose = False)
		self.pidfile = os.path.join(PIDFILE_DIR, self.phonepatch_phpconfig + ".pid")
		self.controlfile = os.path.join(PIDFILE_DIR, self.phonepatch_phpconfig + ".ctl")
		self.language = self.getconf("language")
		self.sounds_dir = self.getconf("sounds_dir")
		self.outcalls_dir = self.getconf("spool_dir") 
		self.festival_gain = self.getconf("festival_audio_gain")
		return phonepatch

	####################################
	def getconf(self, parameter, phpext=None):
		if not phpext:
			phpext = self.phonepatch_extension
		if phpext and phpext in self.phonepatch_extensions and parameter in self.configuration[phpext]:
			return self.configuration[phpext][parameter]
		phpconfig = self.phonepatch_phpconfig
		if phpconfig and phpconfig in self.phonepatch_configs and parameter in self.configuration[phpconfig]:
			return self.configuration[phpconfig][parameter]
		if self.phonepatch_global and parameter in self.configuration[self.phonepatch_global]:
			return self.configuration[self.phonepatch_global][parameter]
		if parameter in self.configuration[self.phonepatch_default]:
			value = self.configuration[self.phonepatch_default][parameter]
			self.debug("getconf: %s not defined, default returned: %s" %(parameter, value))
			return value
		self.debug("getconf: unknown parameter: %s" %parameter)

	####################################
	def set_configuration_sections(self):
		"""Update configuration sections: phonepatch_conf, phonepatch_extension, phonepatch_global"""
		self.phonepatch_configs = []
		self.phonepatch_extensions = []
		self.phonepatch_global = None
		self.phonepatch_default = DEFAULT_SECTION
		for section in self.configuration:
			if section == GLOBAL_SECTION:
				self.phonepatch_global = section
			elif type(section) == str and section.find("phonepatch") == 0:
				self.phonepatch_configs.append(section)
			elif section != DEFAULT_SECTION:
				self.phonepatch_extensions.append(section)

	###############################
	def set_state(self, state):
		"""Set phonepatch state (incall/outcall/daemon)"""
		self.state = state
		if state: self.state_string = state
		else: self.state_string = ""

	###############################
	def debug(self, log, exit=None):
		"""Output debug lines in verbose mode"""
		if not self.verbose: return
		if exit: log = "fatal error - " + log
		if self.state == "daemon" and self.background:
			syslog.syslog(log)
		else:
			log = "phonepatch[%s] - %s" %(self.state_string, log)
			self.secure_os(sys.stderr.write, log+"\n")
			self.secure_os(sys.stderr.flush)
		
		# Exit with code error if "exit" parameter given
		if exit != None: 
			self.debug("clean exit")
			if self.pidfile_created: self.delete_pidfile()
			sys.exit(exit)

	###############################
	def secure_os(self, method, *args):
		while 1:
			try: return method(*args)
			except IOError, e:
				if e.errno == errno.EINTR: continue
				else: raise

	###############################
	def command_output(self, command, input=None):
		"""Run a comand and capture standard output"""
		popen = popen2.Popen3(command)
		if input: self.secure_os(popen.tochild.write, input)
		popen.tochild.close()
		data = self.secure_os(popen.fromchild.read)
		popen.fromchild.close()
		popen.wait()
		return data

	###################################
	def open_radio(self):
		"""Open radio interface and configure PTT"""
		
		self.ctcss_decoder = self.getconf("ctcss_decoder_mintime")
		self.ptt = self.carrier = self.radio_control = None
		control = self.getconf("radio_control")
		if control != "off" and (self.getconf("ptt") or self.getconf("carrier_detection") in ("on", "audio")):
			dtype, lines, device = re.findall("(serial|parallel|command)(.*):(.*)$", control)[0]
			try: dtype, lines, device = re.findall("(serial|parallel|command)(.*):(.*)$", control)[0]
			except: self.debug("open_radio: syntax error on radio_control: %s" %control, exit = 1)
			if lines and lines[0] == "[" and lines[-1] == "]": lines = [x.strip() for x in lines[1:-1].split(",")]
			else: lines = None
			if control.find("serial") == 0:
				self.radio_control = radiocontrol.RadioControl("serial", device, lines)
			elif control.find("parallel") == 0:
				self.radio_control = radiocontrol.RadioControl("parallel", device, lines)
			elif control.find("command") == 0:
				self.command_options = {"set_ptt_on": self.getconf("command_ptt_on"), \
					"set_ptt_off": self.getconf("command_ptt_off"),
					"get_carrier": self.getconf("command_get_carrier"), 
					"get_carrier_response": self.getconf("command_get_carrier_response"), }
				self.radio_control = radiocontrol.RadioControl("command", device, command_options=self.command_options)

			else: self.debug("open_radio: syntax error on radio_control: %s" %control, exit = 1)
			if self.getconf("ptt"):
				self.ptt = Container(set=self.radio_control.set_ptt, get=self.radio_control.get_ptt, \
					threshold = self.getconf("ptt_threshold_signal"), \
					tailtime = self.getconf("ptt_tail_time"), \
					maxtime = self.getconf("ptt_max_time"), \
					waittime = self.getconf("ptt_wait_time"))
			if self.getconf("carrier_detection") in ("on", "audio"):
				self.carrier = Container(type=self.getconf("carrier_detection"), \
					get=self.radio_control.get_carrier, \
					pollingtime=self.getconf("carrier_polling_time"),\
					threshold = self.getconf("carrier_threshold_signal"), \
					tailtime = self.getconf("carrier_tail_time"), \
					maxtime = self.getconf("carrier_max_time"), \
					waittime = self.getconf("carrier_wait_time"))

		# Create radio instance (control soundcard and PTT)
		try:self.radio = radio.Radio(self.getconf("soundcard_device"), self.asterisk_samplerate, \
			self.ptt, self.carrier, verbose=self.modules_verbose, fullduplex = self.getconf("full_duplex"), \
			soundcard_retries = 5, latency = self.getconf("soundcard_latency"), ctcss_mintime=self.ctcss_decoder)
		except Exception, detail:
			self.debug("open_radio: %s" %str(detail), 1)
			sys.exit(1)
			
		self.debug("open_radio: soundcard opened: %s (%s sps)" %(self.getconf("soundcard_device"), self.samplerate))
		#if self.radio_control:
		#	self.debug("open_radio: radio control opened: %s" %control)
			
		# Phonepatch also uses audio_fd, so save it.
		self.audio_fd = self.radio.get_audiofd()
				
	###################################
	def play_text(self, text):
		"""Sintetize text using Festival text-to-speech and return audio data"""
		
		# Festival only supports english, spanish and welsh. 
		# Get long name from ISO code
		audio_data = ""
		command = "echo \"%s\" | festival --tts" %text
		option = self.festival_isotolang.get(self.language, "")
		if option: command += " --language %s" %option

		# Write commands to festival stdin and read from stdout
		s = "(Parameter.set 'Audio_Method 'Audio_Command)\n"
		s += "(Parameter.set 'Audio_Required_Rate %d)\n" %self.asterisk_samplerate
		s += "(Parameter.set 'Audio_Command \"cat $FILE | sox -r $SR \
			-c1 -t raw -2 -s -t raw -c1 -s \")\n" 
		self.debug(s)
		#s = "(SayText \"%s\")\n" %text
		s = ""
		self.radio.set_ptt(True) 
		audio_data = self.command_output(command, input=s)
		self.debug("play_text: festival spawned: %s" %command)
		self.radio.set_ptt(False)
		# Check that festival was succesfully run
		if not audio_data: 
			#self.debug("play_text: festival error")
			return ""
		return audio_data
		
	###################################
	def play_file(self, audio_file):
		"""Load an audio file and returns audio data.
		
		Look for file in that order:
		1) @sounds_dir@/@language@
		2) @sounds_dir@
		"""
		audio_data = ""
		# Look for files
		if os.path.isabs(audio_file):
			paths = [audio_file]
		else:
			paths = [os.path.join(self.sounds_dir, directory, audio_file) for directory in [self.language, ""]]
		
		for cfile in paths:
			if os.path.isfile(cfile):
				break
		else:
			self.debug("play_file: file not found: %s" %audio_file)
			return audio_data
		
		# Convert file to raw format with sox, so the soundcard can play it
		command = "sox %s -t raw -r%d %s -" %(cfile, self.asterisk_samplerate, self.sox_pars)
		self.debug("play_file: sox spawned: %s" %os.path.join(cfile, audio_file))
		audio_data = self.command_output(command)
		if not audio_data: 
			self.debug("play_file: sox returned error")
			audio_data = ""
		return audio_data

	###################################
	def play(self, play_radio=False, play_asterisk=False, args=None, max_time=None, test_function=None, loop=None, flush_asterisk=True):
		"""Play either audio files or text (using festival) and returns bytes written.
		
		play_radio/play_asterisk: Play the sound to the radio and/or asterisk link.
		args: Comma separated string with files or text to play
		max_time: If defined, limit the maximum time to play audio
		test_function: If defined, this function is called every loop; if not succesful, giveup play
		"""
		# Some sanity checks
		if not play_radio and not play_asterisk or args == None: return
		
		if play_radio: self.debug("play: playing to soundcard: %s" %str(args))
		if play_asterisk: self.debug("play: playing to asterisk: %s" %str(args))
		try: args = args.strip().replace("%u", self.getconf("username"))
		except: pass
			
		# args: file | @texttospeech, separed by commas.
		if args == self.last_playargs: 
			# It's the same, so using cached audio data
			self.debug("play: using cached audio")
		else:
			# Load args (play_file() for audiofiles and play_text() for text-to-speech)
			self.raw_data = ""
			for option in [s.strip() for s in args.split(",")]:
				if not option: continue
				if option[0] == self.festival_indicator:
					self.raw_data += self.play_text(option[len(self.festival_indicator):])
				else: self.raw_data += self.play_file(option)
			# If something went wrong, raw_data will have no data
			if not self.raw_data: 
				self.debug("play: audio data is empty, giving up audio play")
				return 0
			self.last_playargs = args
		
		# If max_time defined, calculate maximum amount of bytes to write
		data = self.raw_data
		written = 0
					
		if max_time: 
			max_data = max_time * self.asterisk_samplerate * self.sample_width * self.asterisk_channels
			self.debug("play: playing time limited to %0.2f seconds" %max_time)
		else:
			t = float(len(self.raw_data)) / self.asterisk_samplerate
			self.debug("play: playing audio data (%0.2f seconds)" %t)
		
		txdelay = self.getconf("ptt_txdelay")
		if txdelay and play_radio: 
			txtime = time.time() + self.getconf("ptt_txdelay")
		pttflag = False
		while 1:
			if not data:
				if not loop: break
				data = self.raw_data
			if test_function and not test_function(): 
				written=None; break
			if txdelay and time.time() < txtime:
				continue
			if play_radio and self.ptt and not pttflag: 
				self.radio.set_ptt(True)
				pttflag = True
			if play_radio:
				try: self.radio.send_audio(data[:self.buffer_size], self.ctcss_tx)
				except: self.debug("play: radio send_audio error"); written=None; break
			if play_asterisk:
				if not self.flush_asterisk(False):
					if not play_radio: written=None; break
					play_asterisk = flush_asterisk = False
				try: self.secure_os(self.asterisk_in.write, data[:self.buffer_size]); self.secure_os(self.asterisk_in.flush)
				except: self.debug("play: asterisk write error"); written=None; break
			elif flush_asterisk: self.flush_asterisk()
			
			data = data[self.buffer_size:]
			written += self.buffer_size
			if max_time and written >= max_data:
				break
		
		# Soundcards have internal buffer, make sure they are empty
		try: self.radio.flush_audio()
		except: pass
		
		# If playing to the radio, turn PTT off
		if play_radio and self.ptt and pttflag: 
			self.radio.set_ptt(False)
			
		return written
		
	###################################
	def flush_asterisk(self, write=True):
		try: 
			retsel = select.select([self.asterisk_out], [], [], 0.1)[0]
			if self.asterisk_out not in retsel: return False
			buffer = self.secure_os(os.read, self.asterisk_out.fileno(), self.buffer_size)
			if not buffer: return False
			if write:
				buffer = "\x00" * len(buffer)
				self.secure_os(self.asterisk_in.write, buffer)
				self.secure_os(self.asterisk_in.flush)
			return True
		except: 	return False
	
	###################################
	def draw_power(self, data):
		import audioop
		x=int(audioop.rms(data, 2) /20.0)
		if x > 40: self.debug("*"*x)
		
	#########################################
	def set_gain(self, data, audio_gain):
		"""Apply audio_gain to data"""
		if audio_gain == 1.0:
			return data
		return audioop.mul(data, self.sample_width, audio_gain)

	#########################################
	def get_ctcss(self, ctcss_id):
		if not ctcss_id or ctcss_id == "off": return
		if ctcss_id in PL_CODES: ctcss_freq = PL_CODES[ctcss_id]
		else: ctcss_freq = ctcss_id
		try: ctcss_freq = float(ctcss_freq)
		except: self.debug("get_ctcss: invalid CTCSS frequency: %s" %(str(ctcss_id))); return
		return ctcss_freq

	###########################
	def empty_asterisk(self, t):
		timeout = time.time() + t
		while time.time() < timeout:
			retsel = select.select([self.asterisk_out], [], [], 0.0)
			if self.asterisk_out in retsel[0]:
				data = self.secure_os(os.read, self.asterisk_out.fileno(), self.buffer_size)
				if not data: break

	#########################################
	def audio_loop(self):
		"""Main loop for Asterisk<-> Radio interface"""
		
		if self.getconf("call_limit"): 
			time_limit = time.time() + self.getconf("call_limit")
		else: time_limit = None
		self.debug("audio_loop: soundcard device: %s)" %self.getconf("soundcard_device"))		
		if self.getconf("hangup_button"):
			self.debug("audio_loop: hangup button: %s" %self.getconf("hangup_button"))
		break_reason = None
		asterisk_timeout = 2.0
		asterisk_time = time.time() + asterisk_timeout
		self.radio.reopen_soundcard()
		self.audio_fd = self.radio.get_audiofd()
		input_fds = [self.asterisk_out, self.audio_fd]
		self.empty_asterisk(0.1)
		self.maxocount = 2*self.radio.get_fragmentsize()
		self.debug("audio_loop: start")
		while 1:
			try: retsel = select.select(input_fds, [], [])
			except: self.debug("audio_loop: select error"); break
			if not retsel: self.debug("audio_loop: select returned nothing"); break
			now = time.time()
			if asterisk_time and now > asterisk_time:
				self.debug("audio_loop: asterisk inactivity")
				break_reason = "asterisk"
				break
			
			if self.asterisk_out in retsel[0]:
				# Asterisk -> Radio (with VOX processing)
				asterisk_time = now + asterisk_timeout
				try: data = self.secure_os(os.read, self.asterisk_out.fileno(), self.buffer_size)
				except: data = None
				if not data: self.debug("audio_loop: asterisk closed its read descriptor"); break_reason = "asterisk"; break
				data = self.set_gain(data, self.getconf("radio_audio_gain"))
				# If output buffer is growing, skip the buffer
				count = (self.audio_fd.obufcount() - self.maxocount) & (~1)
				if count <= 0:
					self.radio.vox_toradio(data, self.ctcss_tx)
				else: self.debug("audio_loop: skip buffer")
					
			if self.audio_fd in retsel[0]:
				# Radio -> Asterisk
				
				try: data = self.radio.read_audio(self.buffer_size, self.getconf("radio_audio_limit"))
				except: data = None
				if not data: self.debug("audio_loop: soundcard closed its descriptor"); break_reason = "radio"; break
				data = self.set_gain(data, self.getconf("telephony_audio_gain"))
				try: self.radio.vox_topeer(self.asterisk_in, data)
				except: self.debug("audio_loop: asterisk closed its writing descriptor"); break_reason = "asterisk"; break

				# If hangup_button is configured, hangup line when received
				if self.getconf("hangup_button"):
					keys = self.dtmf_decoder.decode_buffer(data)
					if keys: self.debug("audio_loop: DTMF keys received: %s" %("".join(keys)))
					if self.getconf("hangup_button") in keys:
						self.debug("audio_loop: hangup DTMF button received")
						break_reason = "user"
						break
			
			# If time_limit defined, close interface at that time
			if time_limit and time.time() >= time_limit:
				self.debug("audio_loop: call time-limit reached: %0.2f seconds" %self.getconf("call_limit"))
				break_reason = "timeout"
				break
		
		end_audio = self.getconf("end_audio")
		if break_reason == "asterisk":
			self.play(True, False, end_audio, flush_asterisk=False)
		elif break_reason == "timeout":
			self.play(True, True, end_audio)
		elif break_reason == "user":
			self.play(True, True, end_audio)

		self.debug("audio_loop: end audio loop")

	###################################
	def delete_pidfile(self):
		"""Delete pidfile after a daemon process has finished"""
		try: pidfile = self.pidfile
		except: return
		if not pidfile: return
		try: os.unlink(self.pidfile)
		except OSError, e: 
			if e.errno != errno.ENOENT: raise
		else: self.debug("delete_pidfile: deleted %s" %self.pidfile)
		try: 
			self.control.server_close()
			self.control_enabled = False
		except: pass
		try: os.unlink(self.controlfile)
		except OSError, e: 
			if e.errno != errno.ENOENT: raise
		else: self.debug("delete_pidfile: deleted %s" %self.controlfile)
			
	###################################
	def close_interface(self):
		"""Close asterisk interface (FIFO)"""
		self.debug("close_interface")
		self.radio.set_ptt(False)
		self.asterisk_in.close()
		self.asterisk_out.close()

	###################################
	def control_handler(self, rfile, wfile):
		self.debug("control_handler: start")
		if not self.accept_agicalls:
			self.debug("control_handler: AGI calls not accepted now")
			wfile.write("ko\n")
			return
		s = rfile.readline().strip().split("|")
		self.debug("control_handler: received: %s"%s)
		if len(s) == 2 and s[0] == "incall":
			command, self.phonepatch_extension = s
		elif len(s) == 1 and s[0] == "outcall":
			command = s
		else:
			self.debug("control_handler: syntax error")
			wfile.write("syntax error\n")
			return
		wfile.write("ok")
		self.asterisk_in = wfile
		self.asterisk_out = rfile
		self.call_active = "ask"
		itime = time.time()
		while self.call_active == "ask":
			self.debug("control_handler: waiting for thread access")
			if time.time() > itime + 5.0:
				self.close_interface()
				self.call_active = None
				return
			time.sleep(0.01)
		try:
			if command == "incall":
				if self.process_incall():
					self.audio_loop()
			else: 
				self.audio_loop()
		except Exception, e: self.print_exception(e)
		self.close_interface()
		self.call_active = None


	###################################
	def create_pidfile(self):
		"""Create pidfile when a daemon process starts"""
		self.debug("create_pidfile: %s" %self.pidfile)
		try: fd = open(self.pidfile, "w")
		except: self.debug("create_pidfile: pidfile could not be opened for writing", exit=1)
		fd.write(str(os.getpid()) + "\n")
		fd.close()
		self.pidfile_created = True
		
		ids = self.get_asterisk_id()
		os.chown(self.pidfile, *ids)
		self.control = unixsocket.UnixSocketServer(self.controlfile, reuse=True)
		self.control.set_handler(self.control_handler)
		self.control_enabled = True
		self.control_thread = threading.Thread(target=self.control.serve_forever)
		self.control_thread.setDaemon(True)
		self.control_thread.start()
		os.chown(self.controlfile, *ids)

	###################################
	def read_pidfile(self):
		"""Read pifile and return pid -> Integer"""
		fd = open(self.pidfile)
		pid = int(fd.readline().strip())
		fd.close()
		return pid

	###################################
	def check_ctcss(self, extension=None):
		tone = self.radio.get_ctcss_tone()
		if not tone: return
		if extension == None: extensions = self.phonepatch_extensions
		else: extensions = [extension]
		for section in extensions:
			freq = self.get_ctcss(self.getconf("ctcss_rx", section))
			if freq and freq == tone:
				return section
		if extension == None:
			self.debug("check_ctcss: CTCSS tone %0.1f not found in any phonepatch extension" %tone)
		else: self.debug("check_ctcss: CTCSS tone %0.1f not found in phonepatch extension %d" %(tone, extension))

	###################################
	def set_ctcss_tx(self):
		ctcss_tx_freq = self.get_ctcss(self.getconf("ctcss_tx"))
		ctcss_tx_amplitude = self.get_ctcss(self.getconf("ctcss_tx_amplitude"))
		try: ctcss_tx_freq = float(ctcss_tx_freq)
		except: ctcss_tx_freq = None
		if ctcss_tx_freq and ctcss_tx_amplitude: 
			self.ctcss_tx = ctcss_tx_freq, ctcss_tx_amplitude
			self.debug("loop_daemon: using ctcss_tx tone: %0.1f Hz, amplitude: %0.2f" %self.ctcss_tx)
		else: self.ctcss_tx = None

	###################################
	def process_incall(self):
		self.debug("process_incall: start")
		"""Waits for DTMF answer_button or CTCSS tone (with a timeout) and open the interface if received"""
		if not self.getconf("incall"):
			self.debug("process_incall: incalls disabled for extension: %s" %self.phonepatch_extension)
			return
		calltimeout = time.time() +self.getconf("incall_report_timeout")
		answer_button = self.getconf("incall_answer_button")
		mode = self.getconf("incall_answer_mode")
		self.set_ctcss_tx()
		ctcss_rx_freq = self.get_ctcss(self.getconf("ctcss_rx"))
		if mode == "ctcss" and not ctcss_rx_freq:
			self.debug("process_incall: incall_ctcss_mode set to 'ctcss' but parameter ctcss_rx not defined")
			return
		while 1:
			# TODO: fullduplex
			if self.play(True, True, self.getconf("incall_report_audio")) is None:
				self.debug("process_incall: play() ended abnormally")
				return			
			if not mode or mode == "open": 
				self.debug("process_incall: answer mode set to open, opening channel")
				return "answered"
			elif mode == "dtmf": self.debug("process_incall: waiting for DTMF button: %s" %answer_button)
			elif mode == "ctcss": self.debug("process_incall: waiting for CTCSS tone %0.1f" %ctcss_rx_freq)
			timeout = time.time() + self.getconf("incall_report_audio_wait")
			while time.time() < timeout:
				if not self.flush_asterisk(): 
					self.debug("process_incall: asterisk flush error")
					return
				data = self.radio.read_audio(self.buffer_size)
				if not self.radio.carrier_state: continue
				if mode == "dtmf":
					keys = self.dtmf_decoder.decode_buffer(data)
					for key in keys: self.debug("process_incall: DTMF button received: %s" %key)
					if answer_button in keys:				
						return "answered"
				elif mode == "ctcss":
					self.radio.decode_ctcss(data)
					tone = self.radio.get_ctcss_tone()
					if tone == ctcss_rx_freq:
						self.debug("process_incall: extension ctcss_rx tone %0.1f detected" %ctcss_rx_freq)
						return "answered"
			
			if time.time() > calltimeout:
				self.debug("process_incall: timeout reached: %d seconds" %self.getconf("incall_report_timeout"))
				self.play(True, True, self.getconf("incall_report_timeout_audio"))
				return

	###################################
	def continue_outcall(self):
		"""Callback function to test if an outcall is still active"""
		return (os.path.exists(self.outcallfile) and not self.call_active)

	###################################
	def process_number(self, number):
		# In CTCSS mode phonepatch extension is already set
		if self.phonepatch_extension:
			self.asterisk_extension = self.getconf("outcall_extension").replace("%x", self.phonepatch_extension)
			return number
		# DTMF mode
		if not self.getconf("outcall_dtmf_extension_mode"):
			self.asterisk_extension = self.getconf("outcall_extension")
			return number	
		for phpext in self.phonepatch_extensions:				
			mode = self.getconf("outcall_askfortone_mode", phpext)
			dtmfid = self.getconf("outcall_dtmf_id", phpext).replace("%x", phpext)
			if mode == "dtmf" and dtmfid: 
				if number[:len(dtmfid)] == dtmfid:
					self.debug("process_number: phonepatch prefix match extension: %s" %phpext)
					mode = self.getconf("outcall_askfortone_mode", phpext)
					self.phonepatch_extension = phpext
					number = number[len(dtmfid):]
					self.asterisk_extension = self.getconf("outcall_extension").replace("%x", phpext)
					break
		else: 
			self.debug("process_number: outcall_dtmf_extension_mode enabled and no outcall_dtmf_id prefix matched: %s" %number)
			return
		return number
			
	###################################
	def check_asterisk_active(self):
		try: rv = os.system("ps -C asterisk &>/dev/null") >> 8
		except: return False
		return (rv == 0)
		
	###################################
	def make_call(self, number):
		"""Use outgoing calls Asterisk facility to call number"""
		if not self.check_asterisk_active():
			self.play(True, False, self.getconf("asterisk_inactive_audio"))
			self.debug("make_call: asterisk not active")
			return True

		number = self.process_number(str(number))
		if not number: 
			self.debug("make_call: process_number() not succesful")
			return
		if not self.getconf("outcall"):
			self.debug("make_call: outcalls disabled for extension: %s" %self.phonepatch_extension)
			return
		self.debug("make_call: asterisk extension = %s" %self.asterisk_extension)
		check_script = self.getconf("outcall_check_script")
		if check_script:
			check_script = check_script.replace("%x", self.asterisk_extension)
			self.debug("make_call: executing check_script: %s" %check_script)
			rv, output = run_command(check_script)
			self.debug("make_call: check_script returned code: %d" %rv)
			if rv: self.play(True, False, self.getconf("outcall_check_audio")); return True
		callerid = self.getconf("callerid")
		if not callerid:
			callerid = "%s <%s>" %(self.getconf("username"), self.phonepatch_extension)
		self.debug("make_call: phonepatch: %s (%s) - number: %s" %(self.phonepatch_extension, self.asterisk_extension, number))
		channel = self.getconf("outcall_channel").replace("%x", number)		
		account = OUTCALL_IDNAME
		
		options = [("Channel", channel), ("MaxRetries", "0"), \
			("RetryTime", "60"), ("Context", self.getconf("outcall_context")), \
			("Extension", self.asterisk_extension), ("WaitTime", self.getconf("outcall_timeout")), \
			("Priority", self.getconf("outcall_priority")), ("Account", account), ("CallerID", callerid)]
				
		self.accept_agicalls = True
		# Create a temporal file to write outgoing call options
		tempfd, callpath = tempfile.mkstemp()

		for key, value in options:
			data = "%s: %s" %(key, value)
			os.write(tempfd, data + "\n")
			self.debug("make_call: outcall - %s" %data)
		os.close(tempfd)
		
		# Spool call file must be owned by Asterisk
		gid, uid = self.get_asterisk_id()
		os.chown(callpath, uid, gid)
		
		# Now make the outcall and wait for asterisk response
		self.debug("make_call: start")
		callspool = os.path.join(self.outcalls_dir, os.path.basename(callpath))
		os.rename(callpath, callspool)
		
		# Save outcall spool file name on class object, as callback continue_outcall() uses it
		self.outcallfile = os.path.join(self.outcalls_dir, os.path.basename(callpath))
		
		# TODO: fullduplex
		while 1:
			if not self.continue_outcall(): break
			if self.play(True, False, self.getconf("ring_audio"), max_time=self.getconf("ring_audio_time"), \
				test_function=self.continue_outcall) == None: break
			etime = time.time() + self.getconf("ring_audio_wait")
			
			while time.time() < etime and self.continue_outcall():
				time.sleep(0.1)
				
		self.accept_agicalls = False
		if not self.call_active:
			self.debug("make_call: Asterisk was unable to connect")
			return
		
		self.debug("make_call: Phonepatch AGI launched")
		self.sleep_daemon()
		return True
		
	###################################
	def set_signals(self, signals):
		"""Bind a list of signal to default signal_handler"""
		for sig in signals:
			signal.signal(sig, self.signal_handler)

	#####################################
	def sleep_daemon(self):
		self.call_active = "active"
		while self.call_active:
			time.sleep(0.1)

	#######################################
	def check_daemon(self):
		try: pid = self.read_pidfile()
		except: return
		# Check /proc info to check if it is really a phonepatch daemon running
		statfile = "/proc/%d/stat" % pid 
		try: fd = open(statfile)
		except IOError: self.debug("check_daemon: cannot read process status (%s)" %statfile); return
		name = fd.read().split()[1]
		if name.find("phonepatch") < 0 and name.find("asterisk-phone") < 0 :
			self.debug("check_daemon: pidfile found but not a phonepatch daemon, so deleting it")
			try: os.unlink(self.pidfile)
			except: self.debug("check_daemon: error deleting pidfile" %self.pidfile)
			return
		return pid

	###################################
	def get_asterisk_id(self):
		return pwd.getpwnam("asterisk")[2:4]

	###################################
	def init_daemon(self):
		if self.background: 
			syslog.openlog("phonepatch", syslog.LOG_PID, syslog.LOG_DAEMON)
			self.modules_verbose = False
		
		pid = self.check_daemon()
		if pid: self.debug("init_daemon: phonepatch daemon is already runnning with pid %d" %pid, 1)
			
		# Init flag variables (pause and continue) and set signals
		self.set_signals([signal.SIGTERM, signal.SIGINT])

		gid, uid = self.get_asterisk_id()
		asterisk_groups = [x[2] for x in grp.getgrall() if "asterisk" in x[3]]
		os.setgroups([gid] + asterisk_groups)
		os.setregid(uid, uid)
		os.setreuid(gid, gid)
		self.create_pidfile()
		
	###################################
	def process_noisy_number(self, number, noisy_button):
		"""All repetitions between a noisy_button are 
		removed (and noisy_button itself)"""
		if not noisy_button or type(noisy_button) != str or len(noisy_button) != 1: 
			return number
		output = ""
		memory = None
		for n in number:
			if n == noisy_button and memory != None:
				output += memory
				memory = None
			elif n != noisy_button and memory != None and n != memory: 
				output += memory
				memory = n
			elif n != noisy_button and memory == None:
				memory = n
		if n != noisy_button:
			output = output + n
		return output

	###################################
	def loop_daemon(self):
		# Wait for asktone button, record number and make a call when received outcall_button
		mode = self.getconf("outcall_askfortone_mode")
		button = self.getconf("askfortone_button")
		if not mode: self.debug("loop_daemon: outcall_askfortone_mode not defined"); return
		self.accept_agicalls = True
		while 1:
			self.phonepatch_extension = None
			self.radio.set_ptt(False)
			if mode == "dtmf": self.debug("loop_daemon: waiting askfortone DTMF button: %s" %button)
			if self.ctcss_decoder: self.debug("loop_daemon: CTCSS decoding enabled")
			self.radio.clear_ctcss()
			# CTCSS decoding always done (as it can be enabled inside an extension)
			# DTMF decoding only if asked globally
			while 1:
				if self.call_active:
					self.sleep_daemon()
					continue

				try: data = self.radio.read_audio(self.buffer_size)
				except: self.debug("loop_daemon: error reading from radio"); return
				if mode == "dtmf": # and self.radio.carrier_state:
										
					keys = self.dtmf_decoder.decode_buffer(data)
					for key in keys: self.debug("loop_daemon: DTMF button received: %s" %key)
					if button in keys:				
						break
				self.radio.decode_ctcss(data)
				extension = self.check_ctcss()
				if not extension: continue
				if self.getconf("outcall_askfortone_mode", extension) != "ctcss":
					continue
				number = self.getconf("outcall_ctcss_autocall", extension)
				if number:
					self.accept_agicalls = False
					self.debug("loop_daemon: ctcss autocall: %s" %number)
					self.phonepatch_extension = extension
					if not self.make_call(number):
						self.play(True, False, self.getconf("ring_timeout_audio"))
					self.accept_agicalls = True
					continue
				elif extension:
					self.debug("loop_daemon: ctcss_rx tone detected for extension: %s" %extension)
					self.phonepatch_extension = extension
					break

			self.set_ctcss_tx()
			
			# AskForTone DTMF button received, now record the number
			# TODO: Fullduplex
			if not self.check_asterisk_active():
				self.play(True, False, self.getconf("asterisk_inactive_audio"))
				continue
	 		self.play(True, False, "@KJ5HY A P R S T T.  Begin Your Message")
			#self.play(True, False, self.getconf("tone_audio"), max_time=self.getconf("tone_audio_time"), loop=True)
			#self.play(True, False, self.getconf("tone_audio"), max_time=3, loop=False)
			
			self.debug("loop_daemon: waiting for number and outcall_button")
			timeout_time = time.time() + self.getconf("tone_timeout")
			dtmf_keys = []
		
			while 1:
				now = time.time()
				if now >= timeout_time:
					self.debug("loop_daemon: dial period number timed out")
					self.play(True, False, self.getconf("tone_timeout_audio"))
					break
				data = self.radio.read_audio(self.buffer_size)
				if not data: break
				#if not self.radio.carrier_state: continue
				keys = self.dtmf_decoder.decode_buffer(data)
				dtmf_keys += keys
				for key in keys: 
					self.debug("loop_daemon: DTMF button received: %s (current number: %s)" %(key, "".join(dtmf_keys)))
				if self.getconf("clear_button") in dtmf_keys:
					dtmf_keys = dtmf_keys[dtmf_keys.index(self.getconf("clear_button"))+1:]
					self.debug("loop_daemon: clear_button received, restart dial process")
					continue
				if self.getconf("outcall_button") in dtmf_keys: 
					self.accept_agicalls = False
					dtmf_keys = dtmf_keys[:dtmf_keys.index(self.getconf("outcall_button"))]
					if not dtmf_keys: self.debug("loop_daemon: number void"); continue
					# We have a number (in a list) to call to, convert to string
					number = "".join(dtmf_keys)
					noisy_button = self.getconf("dtmf_noisy_mode_button")
					if noisy_button and noisy_button != "off":
						number = self.process_noisy_number(number, noisy_button)
					self.debug("loop_daemon: outcall_button received, making a call to %s" %number)
					if not self.make_call(number):
						self.play(True, False, self.getconf("ring_timeout_audio"))
					dtmf_keys = []
					self.accept_agicalls = True
					break
		self.accept_agicalls = False

	###################################
	def end_daemon(self):
		self.debug("end_daemon")
		try: self.radio.close()
		except: self.debug("end_daemon: error closing radio")
		self.delete_pidfile()
		self.debug("end_daemon: daemon ended")

	###################################
	def daemon(self, phonepatch, background=False, testcall=None):
		"""Phonepatch acting as daemon.
		
		Listen from radio interface to see if radio-user wants to make a call.
		When an incall or outcall start, this process will be stopped by a signal
		"""
		self.background = background
		if not self.init_php(phonepatch): return
		if not self.getconf("outcall_daemon"):
			if not background:
				self.debug("daemon: outcall_daemon disabled, daemon not loaded for phonepatch: %s" %phonepatch)
			return
		if self.background:
			pid = daemonize.daemonize(return_child=True)
			if pid: return pid
		self.set_state("daemon")
		self.init_daemon()
		self.open_radio()
		
		if testcall != None:
			if not self.make_call(testcall):
				self.play(True, False, self.getconf("ring_timeout_audio"))
			self.debug("daemon: test outcall ended")
			self.delete_pidfile()
			sys.exit(0)

		while 1:
			try: 
				if not self.loop_daemon(): 
					break
			except Exception, e: 
				self.print_exception(e)
			

	####################################
	def print_exception(self, e):
		# In case the phonepatch does not catch a signal
		self.debug("daemon: exception not catched, here are the details:")
		for line in str(e).splitlines():
			self.debug(line)

####################################
def get_phpconfigs(configuration):
	phpconfigs = []
	for section in configuration:
		if type(section) == str and section.find("phonepatch") == 0:
			phpconfigs.append(section)
	return phpconfigs

###################################
def main():
	usage = """
phonepatch [options]

Daemon for the asterisk phonpeatch. It opens the radio interface 
and spawn outgoing calls when required (via DTMF tones)"""
	
	default_template = "/usr/share/asterisk-phonepatch/phonepatch.conf.template"
	default_configuration = "/etc/asterisk/phonepatch.conf"
	
	optpar = optparse.OptionParser(usage)
	optpar.add_option('-q', '--quiet', dest='verbose', default = True, action='store_false', help = 'Be quiet (disable verbose mode)')
	optpar.add_option('-f', '--configuration-file',  dest='configuration_file', type = "string", default = default_configuration, help = 'Use configuration file')
	optpar.add_option('-p', '--phonepatch',  dest='phonepatch',  metavar = 'NAME', default="", type = "string", help = 'Use phonepatch in foreground mode')
	optpar.add_option('-o', '--test-outcall',  dest='test_outcall', metavar = 'NUMBER', type = "string", help = 'Make an outcall test')
	optpar.add_option('-b', '--background',  dest='background',  default = False, action = 'store_true', help = 'Run in background')

	options, args = optpar.parse_args()
	
	config = templateparser.Parser(verbose = True)
	config.read_template(default_template)
	configuration = config.read_configuration(options.configuration_file)
		
	# Run daemon (default), incall or outcall mode
	
	if options.phonepatch:
		phonepatchs = [options.phonepatch]
	else:
		phonepatchs = get_phpconfigs(configuration)
		if not phonepatchs: sys.stderr.write("no phonepatchs found in configuration\n"); sys.exit(1)
		if not options.background: 
			phonepatchs = [phonepatchs[0]]
			sys.stdout.write("using default phonepatch: %s\n" %phonepatchs[0])
	for phpname in phonepatchs:
		php = Phonepatch(configuration, verbose=options.verbose)
		php.daemon(phpname, options.background, options.test_outcall)		
	sys.exit(0)

##############################
## MAIN
#################

if __name__ == "__main__":
	main()

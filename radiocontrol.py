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
import sys, os, optparse
import time, select, re

# External phonepatch modules
sys.path.append("/usr/lib/asterisk-phonepatch")
import processcomm

MODES = ("serial", "parallel", "command")
STATES = ("on", "off")
ONOFF = {False: "off", True: "on"}

DEFAULT_LINES = {"serial": ("rts", "dcd", "dtr"), "parallel": ("data", "busy", "strobe")}

SERIAL_LINES_OUT= {"rts": "setRTS", "dtr": "setDTR"}
SERIAL_LINES_IN= {"dcd": "getCD", "cd": "getCD", "dsr": "getDSR", "cts": "getCTS"}

PARALLEL_LINES_OUT= {"data": "setData", "strobe": "setDataStrobe" , \
	"autofeed": "setAutoFeed", "initialize": "setInitOut"}
PARALLEL_LINES_IN= {"ack": "getInAcknowledge", "busy": "getInBusy", 
	"paperout": "getInPaperOut", "select": "getInSelect", "error": "getInError"}

LINES_IN = {"serial": SERIAL_LINES_IN, "parallel": PARALLEL_LINES_IN}
LINES_OUT = {"serial": SERIAL_LINES_OUT, "parallel": PARALLEL_LINES_OUT}

###########################
def debug(text, verbose=True, exit=None):
	"""Print debug information to standard errror if verbose enabled"""
	if not verbose: return
	sys.stderr.write(text + "\n")
	sys.stderr.flush()
	if exit != None: 
		sys.exit(exit)

###################################
###################################
class RadioControl:
	"""Control PTT (Push-to-Talk) and carrier detection through the serial port, the
	parallel port, or a external command"""
	###################################
	def __init__(self, mode, device, device_lines=None, command_options=None, on_open_wait=0.05):
		"""Returns a RadioControl instance.
		
		mode -- "serial" | "parallel" | "command"
		device -- serial/parallel device or path to command
		device-options -- serial/parallel options for lines: tuple (ptt, carrierdetection, power)
		command_options -- in command mode, enter a string: "PttOn, PttOff, GetCarrier, GetCarrierResponseRegExp"
		"""
		if mode not in MODES:
			raise NameError, "Mode error: %s. Available modes: %s" %(mode, ", ".join(list(MODES)))
		self.mode = mode
		self.device = device
		self.device_lines = {}
		self.command_options = command_options
		if mode == "serial":
			import serial
			self.serial = serial.Serial(device)
			#self.serial.setRTS(True)
			#self.serial.setDTR(True)
			
		elif mode == "parallel":
			import parallel
			self.parallel = parallel.Parallel(device)
		elif mode == "command":
			if self.command_options["get_carrier"]:
				try: self.onstring = re.findall("\((.*)\)", self.command_options["get_carrier_response"])[0].split("|")[0]
				except: raise ValueError, "Syntax error on get_carrier_response: %s" %self.command_options["get_carrier_response"]
			self.command = processcomm.Popen(device)
			try: self.command.read(timeout=0.1)
			except: 
				try: self.command.close()
				except: pass
				self.command = None
			if not self.command:
				raise IOError, "Command could not be started: %s" %self.device

		if mode == "serial" or mode == "parallel":
			if mode == "serial": handler = self.serial
			else: handler = self.parallel
			if not device_lines:
				device_lines = DEFAULT_LINES[mode]

			for index, line in enumerate(["ptt", "carrier", "power"]):
				if index == 0 or index == 2: lines = LINES_OUT[mode]
				else: lines = LINES_IN[mode]
				lines[""] = None
				if index >= len(device_lines): 
					function = None
				else:
					linename = device_lines[index]
					negate = False
					if linename and linename[0] == "-": 
						linename = linename[1:]
						negate = True
					try: function = lines[linename]
					except: raise ValueError, "Error on device_line: %s" %device_lines[index]
				if function: method = getattr(handler, function)
				else: self.device_lines[line] = None; continue
				if method in [x[0] for x in self.device_lines.values()]:
					raise ValueError, "Line already used: %s" %linename
				self.device_lines[line] = (method, negate)
			if self.device_lines["power"]:
				self.device_lines["power"][0](1^self.device_lines["power"][1])
			time.sleep(on_open_wait)
			self.set_ptt(False)

	###################################
	def get_ptt(self):
		"""Get PTT state. It's not really readed from device, which is not 
		possible, just return the class variable stored by set_ptt()"""
		if self.mode == None: 
			raise IOError, "RadioControl is not opened"
		return self.ptt

	###################################
	def get_carrier(self, timeout=0.5):
		"""Get carrier detection state"""
		if self.mode == None: 
			raise IOError, "RadioControl is not opened"
		if self.mode == "serial" or self.mode == "parallel":
			if self.device_lines["carrier"]:
				return self.device_lines["carrier"][0]() ^ self.device_lines["carrier"][1]
		elif self.mode == "command":
			self.command.write(self.command_options["get_carrier"] + "\n")
			self.command.flush()
			maxtime = time.time() + timeout
			while 1:
				try: response = self.command.read()
				except: raise IOError, "Command closed its descriptor"
				for line in response.splitlines():
					value = re.findall(self.command_options["get_carrier_response"], line)
					if value: return self.onstring == value[0]
				now = time.time()
				if now >= maxtime: break
			raise IOError, "Cannot get carrier-detection state from command"
		
	###################################
	def set_ptt(self, state, timeout = 0.5):
		"""Set PTT state (True/False)"""
		#The following 2 lines is for the Rigblaster.  Added by Stephen Hamilton
		self.serial.setDTR(False)
		self.serial.setRTS(False)
		if self.mode == None: 
			raise IOError, "RadioControl is not opened"
		state = int(bool(state))
		self.ptt = state
		if self.mode == "serial" or self.mode == "parallel":
			if self.device_lines["ptt"]:
				self.device_lines["ptt"][0](state ^ self.device_lines["ptt"][1])
				#debug("Test")
				#self.serial.close()
		elif self.mode == "command":
			key = "set_ptt_%s" %(ONOFF[state])
			self.command.write(self.command_options[key]  + "\n")
			self.command.flush()
		
	###################################
	def close(self):
		if self.mode == None: 
			raise IOError, "RadioControl is not opened"
		if self.mode == "serial":
			self.serial.close()
		elif self.mode == "parallel":
			del self.parallel
		elif self.mode == "command":
			self.command.close()
		self.mode = None

###################################
def output(text):
	sys.stdout.write(text + "\n")
	sys.stdout.flush()

###################################
def server(radio, verbose):
	"""Allowed commands:
	set ptt on: Set PTT on (return "done" if succesful)
	set ptt off: Set PTT off (return "done" if succesful)
	get carrier: Return Carrier-Detection state (return "carrier: 0" or "carrier: 1" if succesful)"""
	debug("starting server mode", verbose)
	while 1:
		line = sys.stdin.readline()
		if not line: break
		line = line.strip()
		if line.find("set ptt ") == 0:
			if line == "set ptt on": radio.set_ptt(True)
			elif line == "set ptt off": radio.set_ptt(False)
			else: output("syntax error: %s" %line); continue
			output("done: %s" %line)
		elif line == "get carrier":
			ret = radio.get_carrier()
			output("get carrier: %d" %int(ret))
		elif line == "":
			continue
		else: output("unknown command: %s" %line)
	debug("end of server mode", verbose)

###################################
def get_lines_description():
	output = ""
	SERIAL_LINES={"input": SERIAL_LINES_IN, "output": SERIAL_LINES_OUT}
	PARALLEL_LINES={"input": PARALLEL_LINES_IN, "output": PARALLEL_LINES_OUT}
	DEVICES={"serial": SERIAL_LINES, "paralell": PARALLEL_LINES}

	for device, lines in DEVICES.items():
		output += "    device mode: %s\n" %device
		for direction, line in lines.items():
			names = line.keys()
			output += "        %s lines: %s\n" %(direction, ", ".join(names))
			
	return output

###################################
def main():
	print("Test: ");
	lat = "3322.99"
	latf = float(lat) + .01
	lats = "%4.2f" % latf
	print(lats)
	usage = """radio-control.py [options]


Control radio PTT (output) and carrier-detection (input) using a serial, 
parallel port or an external command. Default lines are (change them
with --device-lines):

Serial -- RTS for PTT, CTS for carrier-detection, DTR for carrier-power.
Parallel -- D0 for PTT, Busy for carrier-detecion, Strobe for carrier-power.

Available lines: 
%s
Server mode accepts the following commands:

set ptt on --  Set PTT on
set ptt off --  Set PTT off
get carrier --  Get Carrier-Detection state. Returns: "get carrier: 0|1" """ %get_lines_description()
	
	optpar = optparse.OptionParser(usage)
	optpar.add_option('-v', '--verbose', dest='verbose', default = False, action='store_true', help = 'Be verbose')
	optpar.add_option('-m', '--port-mode',  dest='mode', type = "string", default = "", metavar = 'MODE', help = 'Port mode: %s' %" | ". join(list(MODES)))
	optpar.add_option('-d', '--device',  dest='device', type = "string", default = "", metavar = 'DEVICE', help = 'Device file')
	optpar.add_option('-p', '--ptt',  dest='ptt', type = "string", default = "", metavar = 'STATE', help = 'Set new PTT state (on|off)')
	optpar.add_option('-c', '--carrier-detection',  dest='carrier',  default = False, action = 'store_true', help = 'Get carrier-detection state')
	optpar.add_option('-w', '--wait-time',  dest='wait', type = "float", default = None, metavar = 'SECONDS', help = 'On PTT mode, time to wait before exit. On Carrier detection, time before reading')
	optpar.add_option('-s', '--server',  dest='server',  default = False, action = 'store_true', help = 'Start in server mode')
	optpar.add_option('-o', '--command-options',  dest='command_options', type = "string", default = "", metavar = 'OPTIONS', help = 'Strings for command mode: ptt-on,ptt-off,get-carrier,get-carrier-response-regexp')
	optpar.add_option('', '--device-lines',  dest='device_lines', type = "string", default = "", metavar = 'OPTIONS', help = 'Strings for serial/parallel lines (start with "-" to negate line): ptt,get-carrier,carrier-power')
	
	options, args = optpar.parse_args()
	verbose = options.verbose

	if not options.mode: 
		options.mode = "serial"
	if options.mode not in MODES:
		optpar.print_help()
		debug("\nSupported port modes: %s" %", ".join(list(MODES)), exit = 1)
	if options.ptt and options.ptt not in STATES:
		optpar.print_help()
		debug("\nSupported PTT states: %s" % ", ".join(list(STATES)), exit = 1)
	if not options.device:
		if options.mode == "serial": options.device = "/dev/ttyS0"
		elif options.mode == "serial":  options.device = "/dev/parport0"
		elif options.mode == "command": 
			optpar.print_help()
			debug("You must specify a command (in device option) for command mode", exit = 1)
	
	if options.device_lines:
		device_lines = [x.strip().lower() for x in options.device_lines.split(",")]
		if len(device_lines) > 3:
			debug("Syntax error on device_lines: %s" %options.device_lines, exit = 1)
	else: device_lines = None

	if options.mode == "command":
		if not options.command_options:
			optpar.print_help()
			debug("You must specify command_options for command mode (leave not used fields void)", exit = 1)
		try: ptton, pttoff, getcarrier,getcarrierreturn = [x.strip() for x in options.command_options.split(",")]
		except: optpar.print_help(); debug("Syntax error on command_options: %s" %options.command_options, exit = 1)
		command_options = {"set_ptt_on": ptton, "set_ptt_off": pttoff, "get_carrier": getcarrier, "get_carrier_response": getcarrierreturn}
	else: command_options = None
		
	if not options.ptt and not options.carrier and not options.server:
		optpar.print_usage()
		sys.exit(1)

	debug("options - mode: %s" %options.mode, verbose)
	debug("options - device: %s" %options.device, verbose)
	if device_lines: debug("options - device-lines: %s" %device_lines, verbose)
	if options.wait != None: debug("options - wait time: %s" %str(options.wait), verbose)
	debug("opening device: %s" %options.device, verbose)
	rc = RadioControl(options.mode, options.device, device_lines, command_options)
	debug("device opened: %s" %options.device, verbose)

	if options.server: 
		server(rc, verbose)
		rc.close(); sys.exit(0)

	if options.ptt:
		rc.set_ptt(options.ptt == "on")
		debug("ptt set: %s" %options.ptt, verbose)
		if options.wait == None: 
			debug("press enter to exit")
			raw_input()
		else:
			debug("waiting: %d seconds" %options.wait, verbose)
			time.sleep(options.wait)
		
	if options.carrier:
		if options.wait != None:
			debug("waiting: %d seconds" %options.wait, verbose)
			time.sleep(options.wait)
		try: state = rc.get_carrier()
		except: output("error getting carrier state")
		else: output("get carrier: %d" %int(state))
	
	rc.close()
	sys.exit(0)

##############################
## MAIN
#################

if __name__ == "__main__":
	main()

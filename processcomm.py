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
import os, select, time
import popen2, signal

__version__ = "$Revision: 1.3 $"
__author__ = "Arnau Sanchez <arnau@ehas.org>"
__depends__ = ['Python-2.4']
__copyright__ = """Copyright (C) 2006 Arnau Sanchez <arnau@ehas.org>.
This code is distributed under the terms of the GNU General Public License."""

###############################
###############################
class Popen:
	"""Provide interface for process command execution"""
	
	###############################
	def __init__(self, command):
		self.popen = popen2.Popen3(command)
		self.onoff_dict = {False: "off", True: "on"}

	###############################
	def read(self, buffer_size = 256, timeout = 0):
		"""Read data from a process without blocking"""
		fileno = self.popen.fromchild.fileno()
		buffer = ""
		while 1:
			retsel = select.select([fileno], [], [], timeout)
			if not retsel or fileno not in retsel[0]: break
			tbuffer = os.read(fileno, buffer_size)
			if not tbuffer:
				self.popen = None
				raise IOError, "Command closed its descriptor"
			buffer += tbuffer
		return buffer

	###################################
	def write(self, data):
		if not self.popen:
			raise IOError, "Command is not running"
		self.popen.tochild.write(data)

	###################################
	def flush(self):
		if not self.popen:
			raise IOError, "Command is not running"
		self.popen.tochild.flush()

	###################################
	def close(self, killtime = 1.0):
		if not self.popen:
			raise IOError, "Command is not running"		
		
		# Process should finish after closing its descriptors
		self.popen.fromchild.close()
		self.popen.tochild.close()
		
		# For security, give the process a short time (killtime) to stop, later kill it
		tkilltime = time.time() + killtime
		while time.time() < tkilltime and self.popen.poll() == None:
			time.sleep(killtime/10.0)
		if self.popen.poll() == None:
			try: os.kill(self.popen.pid, signal.SIGKILL)
			except: pass
		retval = self.popen.wait()
		self.popen = None
		return retval

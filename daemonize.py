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

import os, sys

__version__ = "$Revision: 1.2 $"
__author__ = "Arnau Sanchez <arnau@ehas.org>"
__depends__ = ['Python-2.3']
__copyright__ = """Copyright (C) 2006 Arnau Sanchez <arnau@ehas.org>.
This code is distributed under the terms of the GNU General Public License."""

#######################################
def error(line):
	sys.stderr.write(line)
	sys.stderr.flush()
	

#######################################
def daemonize(pidfile = "", return_child=False):
	# First fork
	try: 
		pid = os.fork() 
		if pid > 0: sys.exit(0)
	except OSError, detail: 
		error("fork #1 failed: (%d) %s\n" % (detail.errno, detail.strerror))
		sys.exit(1)
		
	# Decouple from parent environment.
	os.chdir("/") 
	os.umask(0) 
	os.setsid() 
	
	# Do second fork.
	try: 
		pid = os.fork() 
		if pid > 0:
			if return_child: return pid
			else: sys.exit(0)
	except OSError, detail: 
		error("fork #2 failed: (%d) %s\n" % (detail.errno, detail.strerror))
		sys.exit(1)
	
	# Redirect standard file descriptors.
	si = file("/dev/null", 'r')
	so = file("/dev/null", 'w')	
	
	os.dup2(si.fileno(), sys.stdin.fileno())
	os.dup2(so.fileno(), sys.stdout.fileno())
	os.dup2(so.fileno(), sys.stderr.fileno())

	# If no pidfile to write, all is done
	if not pidfile:  return
	
	# Open pidfile and write PID value
	try: fd = open(pidfile, "w")
	except OSError: 
		error( "pidfile (%s) creation failed: (%d) %s\n" % (pidfile, detail.errno, detail.strerror))
		sys.exit(1)
			
	fd.write( str(os.getpid()) + "\n")
	fd.close()
#!/usr/bin/python

# This file is part of asterisk-phonepatch

# Copyright (C) 2011 Stephen Hamilton
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
import socket 
__version__ = "$Revision: 0.1 $"
__author__ = "Stephen Hamilton <stephenshamilton@gmail.com>"
__depends__ = ['Python-2.4']
__copyright__ = """Copyright (C) 2011 Stephen Hamilton.
This code is distributed under the terms of the GNU General Public License."""

###############################
###############################
class Aprs:
	def __init__(self):
		self.serverHost = 'second.aprs.net'
        	self.serverPort = 10151
        	self.password = '21728'
        	self.callsign = 'KJ5HY-2'
        	self.latitude = '4122.90'
        	self.longitude = '07358.20'
        	self.latquad = "N"
        	self.longquad = "W"
        	# Comment should be 53 char or less
        	self.comment = 'KJ5HY APRStt'
        	self.packet = ''		

	def send_packet(self, aprs_callsign, symbol, station_number):
		# create socket & connect to server
		sSock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		sSock.connect((self.serverHost, self.serverPort))
		#Pause
		# logon
		sSock.send('user ' + self.callsign + ' pass ' + self.password + ' vers "KJ5HY APRStt .1"\n')
       		 # calculate position underneath APRStt station
		#Float conversions to add.  Ensure 0's aren't lost too!
		#stationlat = self.latitude - (.10 * station_number)
		stationlat = float(self.latitude) - (station_number*.01)
		stationlat = '%4.2f' % stationlat
		print(stationlat)
		# Form address for APRS packet.  Add -12 for TT user.
		address =  aprs_callsign + '-12>APT001,WIDE1-1,qAR,' + self.callsign
		position = ':!' + stationlat + self.latquad + '/' + self.longitude + self.longquad + symbol
		#ID Packet
		#working: id = self.callsign + '>APT001,TCPIP*:=4122.90N/07358.20WxAPRStt-Testing'
		id = self.callsign + '>APT001,TCPIP*:=' + str(self.latitude) + self.latquad + '/' + str(self.longitude) + self.longquad + 'r146.58 MHz\n'
		print (id)
		sSock.send(id + '\n')
		#time.sleep(1)
		#send packet
		print(address + position)
		sSock.send(address + position + '\n')
		print("packet sent: " + time.ctime() )
        	# close socket -- must be closed to avoid buffer overflow
		#time.sleep(2)
		sSock.shutdown(0)
		sSock.close()
 

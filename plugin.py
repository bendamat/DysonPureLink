# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is furnished
# to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# Author: Jan-Jaap Kostelijk
#
# Domoticz plugin to handle communction to Dyson devices
#
"""
<plugin key="DysonPureLink" name="Dyson Pure Link" author="Jan-Jaap Kostelijk" wikilink="https://github.com/JanJaapKo/DysonPureLink/wiki" externallink="https://github.com/JanJaapKo/DysonPureLink">
    <description>
        <h2>Dyson Pure Link plugin</h2><br/>
        Connects to Dyson Pure Link devices.
        It reads the machine's states and sensors and it can control it via commands.<br/><br/>
		This plugin has been tested with a PureCool type 475 (pre 2018), it is assumed the other types work too. There are known issues in retreiving information from the cloud account, see git page for the issues.<br/><br/>
        <h2>Configuration</h2>
        Configuration of the plugin is a 2 step action due to the 2 factor authentication. First, provide all in step A and when you receive an email, proceed wuith step B. See the Wiki for more info.<br/><br/>
        <ol type="A">
            <li>provide always the following information:</li>
            <ol>
                <li>the machine's IP adress</li>
                <li>the port number (should normally remain 1883)</li>
                <li>enter the email adress under "Cloud account email adress"</li>
                <li>enter the password under "Cloud account password"</li>
                <li>optional: enter the machine's name under "machine name" when there is more than 1 machines linked to the account</li>
            </ol>
            <li>When you have received a verification cpode via email, supply it once when recieved (can be removed after use):</li>
            <ol>
                <li>enter the received code under "email verification code"</li>
            </ol>
        </ol>
        
    </description>
    <params>
		<param field="Address" label="IP Address" required="true"/>
		<param field="Port" label="Port" width="30px" required="true" default="1883"/>
		<param field="Mode5" label="Cloud account email adress" default="sinterklaas@gmail.com" width="300px" required="false"/>
        <param field="Mode3" label="Cloud account password" required="false" default="" password="true"/>
		<param field="Mode1" label="email verification code" width="75px" default="0"/>
        <param field="Mode6" label="Machine name (cloud account)" required="false" default=""/>
		<param field="Mode4" label="Debug" width="75px">
            <options>
                <option label="Verbose" value="Verbose"/>
                <option label="True" value="Debug"/>
                <option label="False" value="Normal" default="true"/>
                <option label="Reset cloud data" value="Reset"/>
            </options>
        </param>
        <param field="Mode2" label="Refresh interval" width="75px">
            <options>
                <option label="20s" value="2"/>
                <option label="1m" value="6"/>
                <option label="5m" value="30" default="true"/>
                <option label="10m" value="60"/>
                <option label="15m" value="90"/>
            </options>
        </param>
    </params>
</plugin>
"""

try:
	import Domoticz
	debug = False
except ImportError:
	import fakeDomoticz as Domoticz
	debug = True
import json
import time
from mqtt import MqttClient
from dyson_pure_link_device import DysonPureLinkDevice
from cloud.account import DysonAccount

from value_types import SensorsData, StateData

class DysonPureLinkPlugin:
    #define class variables
    #plugin version
    version = "4.0.1"
    enabled = False
    mqttClient = None
    #unit numbers for devices to create
    #for Pure Cool models
    fanModeUnit = 1
    nightModeUnit = 2
    fanSpeedUnit = 3
    fanOscillationUnit = 4
    standbyMonitoringUnit = 5
    filterLifeUnit = 6
    qualityTargetUnit = 7
    tempHumUnit = 8
    volatileUnit = 9
    particlesUnit = 10
    sleepTimeUnit = 11
    fanStateUnit = 12
    fanFocusUnit = 13
    fanModeAutoUnit = 14
    particles2_5Unit = 15
    particles10Unit = 16
    nitrogenDioxideDensityUnit = 17
    heatModeUnit = 18
    heatTargetUnit = 19
    heatStateUnit = 20
    particlesMatter25Unit = 21
    particlesMatter10Unit = 22

    runCounter = 6
    pingCounter = 3

    def __init__(self):
        self.myDevice = None
        self.password = None
        self.ip_address = None
        self.port_number = None
        self.sensor_data = None
        self.state_data = None
        self.mqttClient = None
        self.log_level = None

    def onStart(self):
        Domoticz.Debug("onStart called")
        #read out parameters for local connection
        self.ip_address = Parameters["Address"].strip()
        self.port_number = Parameters["Port"].strip()
        self.otp_code = Parameters['Mode1']
        self.runCounter = int(Parameters['Mode2'])
        self.log_level = Parameters['Mode4']
        self.pingCounter = int(self.runCounter/2)
        self.account_password = Parameters['Mode3']
        self.account_email = Parameters['Mode5']
        self.machine_name = Parameters['Mode6']
        
        if self.log_level == 'Debug':
            Domoticz.Debugging(2)
            DumpConfigToLog()
        if self.log_level == 'Verbose':
            Domoticz.Debugging(1+2+4+8+16+64)
            DumpConfigToLog()
        if self.log_level == 'Reset':
            Domoticz.Log("Plugin config will be erased to retreive new cloud account data")
            Config = {}
            Config = Domoticz.Configuration(Config)
                
        #PureLink needs polling, get from config
        Domoticz.Heartbeat(10)
        
        self.checkVersion(self.version)
        
        mqtt_client_id = ""
        
        #create a Dyson account
        deviceList = self.get_device_names()

        if deviceList != None and len(deviceList)>0:
            Domoticz.Debug("Number of devices found in plugin configuration: '"+str(len(deviceList))+"'")
        else:
            Domoticz.Log("No devices found in plugin configuration, request from Dyson cloud account")

            #new authentication
            Domoticz.Debug("=== start making connection to Dyson account, new method as of 2021 ===")
            dysonAccount2 = DysonAccount()
            challenge_id = getConfigItem(Key="challenge_id", Default = "")
            setConfigItem(Key="challenge_id", Value = "") #clear after use
            if challenge_id == "":
                #request otp code via email when no code entered
                challenge_id = dysonAccount2.login_email_otp(self.account_email, "NL")
                setConfigItem(Key="challenge_id", Value = challenge_id)
                Domoticz.Log('==== An OTP verification code had been requested, please check email and paste code into plugin=====')
                return
            else:
                #verify the received code
                if len(self.otp_code) < 6:
                    Domoticz.Error("invalid verification code supplied")
                    return
                dysonAccount2.verify(self.otp_code, self.account_email, self.account_password, challenge_id)
                setConfigItem(Key="challenge_id", Value = "") #reset challenge id as it is no longer valid
                Parameters['Mode1'] = "0" #reset the stored otp code
                #get list of devices info's
                deviceList = dysonAccount2.devices()
                deviceNames = list(deviceList.keys())
                Domoticz.Log("Received new devices: " + str(deviceNames) + ", they will be stored in plugin configuration")
                i=0
                for device in deviceList:
                    setConfigItem(Key="{0}.name".format(i), Value = deviceNames[i]) #store the name of the machine
                    Domoticz.Debug('Key="{0}.name", Value = {1}'.format(i, deviceNames[i])) #store the name of the machine
                    setConfigItem(Key="{0}.credential".format(deviceList[deviceNames[i]].name), Value = deviceList[deviceNames[i]].credential) #store the credential
                    Domoticz.Debug('Key="{0}.credential", Value = {1}'.format(deviceList[deviceNames[i]].name, deviceList[deviceNames[i]].credential)) #store the credential
                    setConfigItem(Key="{0}.serial".format(deviceList[deviceNames[i]].name), Value = deviceList[deviceNames[i]].serial) #store the serial
                    Domoticz.Debug('Key="{0}.serial", Value =  {1}'.format(deviceList[deviceNames[i]].name, deviceList[deviceNames[i]].serial)) #store the serial
                    setConfigItem(Key="{0}.product_type".format(deviceList[deviceNames[i]].name), Value = deviceList[deviceNames[i]].product_type) #store the product_type
                    Domoticz.Debug('Key="{0}.product_type" , Value = {1}'.format(deviceList[deviceNames[i]].name, deviceList[deviceNames[i]].product_type)) #store the product_type
                    i = i + 1

        if deviceList == None or len(deviceList)<1:
            Domoticz.Error("No devices found in plugin configuration or Dyson cloud account")
            return
        else:
            Domoticz.Debug("Number of devices in plugin: '"+str(len(deviceList))+"'")

        if deviceList != None and len(deviceList) > 0:
            if len(self.machine_name) > 0:
                if self.machine_name in deviceList:
                    password, serialNumber, deviceType= self.get_device_config(self.machine_name)
                    Domoticz.Debug("password: {0}, serialNumber: {1}, deviceType: {2}".format(password, serialNumber, deviceType))
                    self.myDevice = DysonPureLinkDevice(password, serialNumber, deviceType, self.machine_name)
                else:
                    Domoticz.Error("The configured device name '" + self.machine_name + "' was not found in the cloud account. Available options: " + str(list(deviceList)))
                    return
            elif len(deviceList) == 1:
                self.myDevice = deviceList[list(deviceList)[0]]
                Domoticz.Log("1 device found in plugin, none configured, assuming we need this one: '" + self.myDevice.name + "'")
            else:
                #more than 1 device returned in cloud and no name configured, which the the plugin can't handle
                Domoticz.Error("More than 1 device found in cloud account but no device name given to select. Select and filter one from available options: " + str(list(deviceList)))
                return
            #the Domoticz connection object takes username and pwd from the Parameters so write them back
            Parameters['Username'] = self.myDevice.serial #take username from account
            Parameters['Password'] = self.myDevice.password #override the default password with the one returned from the cloud
        else:
            Domoticz.Error("No usable credentials found")
            return

        #check, per device, if it is created. If not,create it
        Options = {"LevelActions" : "|||",
                   "LevelNames" : "|OFF|ON|AUTO",
                   "LevelOffHidden" : "true",
                   "SelectorStyle" : "1"}
        if self.fanModeUnit not in Devices:
            Domoticz.Device(Name='Fan mode', Unit=self.fanModeUnit, TypeName="Selector Switch", Image=7, Options=Options).Create()
        if self.fanStateUnit not in Devices:
            Domoticz.Device(Name='Fan state', Unit=self.fanStateUnit, Type=244, Subtype=62, Image=7, Switchtype=0).Create()
        if self.heatStateUnit not in Devices:
            Domoticz.Device(Name='Heating state', Unit=self.heatStateUnit, Type=244, Subtype=62, Image=7, Switchtype=0).Create()
        if self.nightModeUnit not in Devices:
            Domoticz.Device(Name='Night mode', Unit=self.nightModeUnit, Type=244, Subtype=62,  Switchtype=0, Image=9).Create()
            
        Options = {"LevelActions" : "|||||||||||",
            "LevelNames" : "OFF|1|2|3|4|5|6|7|8|9|10|AUTO",
            "LevelOffHidden" : "false",
            "SelectorStyle" : "1"}
        if self.fanSpeedUnit not in Devices:
            Domoticz.Device(Name='Fan speed', Unit=self.fanSpeedUnit, TypeName="Selector Switch", Image=7, Options=Options).Create()

        if self.fanOscillationUnit not in Devices:
            Domoticz.Device(Name='Oscilation mode', Unit=self.fanOscillationUnit, Type=244, Subtype=62, Image=7, Switchtype=0).Create()
        if self.standbyMonitoringUnit not in Devices:
            Domoticz.Device(Name='Standby monitor', Unit=self.standbyMonitoringUnit, Type=244, Subtype=62,Image=7, Switchtype=0).Create()
        if self.filterLifeUnit not in Devices:
            Domoticz.Device(Name='Remaining filter life', Unit=self.filterLifeUnit, TypeName="Custom").Create()
        if self.tempHumUnit not in Devices:
            Domoticz.Device(Name='Temperature and Humidity', Unit=self.tempHumUnit, TypeName="Temp+Hum").Create()
        if self.volatileUnit not in Devices:
            Domoticz.Device(Name='Volatile organic', Unit=self.volatileUnit, TypeName="Air Quality").Create()
        if self.sleepTimeUnit not in Devices:
            Domoticz.Device(Name='Sleep timer', Unit=self.sleepTimeUnit, TypeName="Custom").Create()

        if self.particlesUnit not in Devices:
            Domoticz.Device(Name='Dust', Unit=self.particlesUnit, TypeName="Air Quality").Create()
        if self.qualityTargetUnit not in Devices:
            Options = {"LevelActions" : "|||",
                       "LevelNames" : "|Normal|Sensitive (Medium)|Very Sensitive (High)|Off",
                       "LevelOffHidden" : "true",
                       "SelectorStyle" : "1"}
            Domoticz.Device(Name='Air quality setpoint', Unit=self.qualityTargetUnit, TypeName="Selector Switch", Image=7, Options=Options).Create()

        if self.particles2_5Unit not in Devices:
            Domoticz.Device(Name='Dust (PM 2,5)', Unit=self.particles2_5Unit, TypeName="Air Quality").Create()
        if self.particles10Unit not in Devices:
            Domoticz.Device(Name='Dust (PM 10)', Unit=self.particles10Unit, TypeName="Air Quality").Create()
        if self.particlesMatter25Unit not in Devices:
            Domoticz.Device(Name='Particles (PM 25)', Unit=self.particlesMatter25Unit, TypeName="Air Quality").Create()
        if self.particlesMatter10Unit not in Devices:
            Domoticz.Device(Name='Particles (PM 10)', Unit=self.particlesMatter10Unit, TypeName="Air Quality").Create()
        if self.fanModeAutoUnit not in Devices:
            Domoticz.Device(Name='Fan mode auto', Unit=self.fanModeAutoUnit, Type=244, Subtype=62, Image=7, Switchtype=0).Create()
        if self.fanFocusUnit not in Devices:
            Domoticz.Device(Name='Fan focus mode', Unit=self.fanFocusUnit, Type=244, Subtype=62, Image=7, Switchtype=0).Create()
        if self.nitrogenDioxideDensityUnit not in Devices:
            Domoticz.Device(Name='Nitrogen Dioxide Density (NOx)', Unit=self.nitrogenDioxideDensityUnit, TypeName="Air Quality").Create()
        if self.heatModeUnit not in Devices:
            Options = {"LevelActions" : "||",
                       "LevelNames" : "|Off|Heating",
                       "LevelOffHidden" : "true",
                       "SelectorStyle" : "1"}
            Domoticz.Device(Name='Heat mode', Unit=self.heatModeUnit, TypeName="Selector Switch", Image=7, Options=Options).Create()
        if self.heatTargetUnit not in Devices:
            Domoticz.Device(Name='Heat target', Unit=self.heatTargetUnit, Type=242, Subtype=1).Create()

        Domoticz.Log("Device instance created: " + str(self.myDevice))
        self.base_topic = self.myDevice.device_base_topic
        Domoticz.Debug("base topic defined: '"+self.base_topic+"'")

        #create the connection
        if self.myDevice != None:
            self.mqttClient = MqttClient(self.ip_address, self.port_number, mqtt_client_id, self.onMQTTConnected, self.onMQTTDisconnected, self.onMQTTPublish, self.onMQTTSubscribed)
    
    def onStop(self):
        Domoticz.Debug("onStop called")

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Debug("DysonPureLink plugin: onCommand called for Unit " + str(Unit) + ": Parameter '" + str(Command) + "', Level: " + str(Level))
        topic = ''
        payload = ''
        arg = '' 
        fan_pwr_list = ['438','520','527'] 
        
        if Unit == self.qualityTargetUnit and Level<=100:
            topic, payload = self.myDevice.set_quality_target(Level)
        if Unit == self.fanSpeedUnit and Level<=100:
            arg="0000"+str(Level//10)
            topic, payload = self.myDevice.set_fan_speed(arg[-4:]) #use last 4 characters as speed level or AUTO
            self.mqttClient.Publish(topic, payload)
            if Level>0:
                #when setting a speed value, make sure that the fan is actually on
                if self.myDevice.product_type in fan_pwr_list:
                    topic, payload = self.myDevice.set_fan_power("ON") 
                else:
                    topic, payload = self.myDevice.set_fan_mode("FAN") 
            else:
                if self.myDevice.product_type in fan_pwr_list:
                    topic, payload = self.myDevice.set_fan_power("OFF") 
                else:
                    topic, payload = self.myDevice.set_fan_mode("OFF") #use last 4 characters as speed level or AUTO
        if Unit == self.fanModeUnit or (Unit == self.fanSpeedUnit and Level>100):
            if self.myDevice.product_type in fan_pwr_list:
                if Level >= 30: 
                    arg="ON"
                    #Switch to Auto
                    topic, payload = self.myDevice.set_fan_power(arg) 
                    self.mqttClient.Publish(topic, payload)
                    topic, payload = self.myDevice.set_fan_mode_auto(arg) 
                elif Level == 20:
                    arg="ON"
                    #Switch on, auto depends on previous setting
                    topic, payload = self.myDevice.set_fan_power(arg) 
                else:
                    #Switch Off
                    arg='OFF'
                    topic, payload = self.myDevice.set_fan_power(arg) 
            else:
                if Level == 10: arg="OFF"
                if Level == 20: arg="FAN"
                if Level >=30: arg="AUTO"
                topic, payload = self.myDevice.set_fan_mode(arg) 
        if Unit == self.fanStateUnit:
            Domoticz.Log("Unit Fans State is read only, no command sent")
        if Unit == self.fanOscillationUnit:
            topic, payload = self.myDevice.set_oscilation(str(Command).upper()) 
        if Unit == self.fanFocusUnit:
            topic, payload = self.myDevice.set_focus(str(Command).upper()) 
        if Unit == self.fanModeAutoUnit:
            topic, payload = self.myDevice.set_fan_mode_auto(str(Command).upper()) 
        if Unit == self.standbyMonitoringUnit:
            topic, payload = self.myDevice.set_standby_monitoring(str(Command).upper()) 
        if Unit == self.nightModeUnit:
            topic, payload = self.myDevice.set_night_mode(str(Command).upper()) 
        if Unit == self.heatModeUnit:
            if Level == 10: arg="OFF"
            if Level == 20: arg="HEAT"
            topic, payload = self.myDevice.set_heat_mode(arg) 
        if Unit == self.heatTargetUnit:
            topic, payload = self.myDevice.set_heat_target(Level) 

        self.mqttClient.Publish(topic, payload)

    def onConnect(self, Connection, Status, Description):
        Domoticz.Debug("onConnect called: Connection '"+str(Connection)+"', Status: '"+str(Status)+"', Description: '"+Description+"'")
        self.mqttClient.onConnect(Connection, Status, Description)

    def onDisconnect(self, Connection):
        self.mqttClient.onDisconnect(Connection)

    def onMessage(self, Connection, Data):
        self.mqttClient.onMessage(Connection, Data)

    def onNotification(self, Name, Subject, Text, Status, Priority, Sound, ImageFile):
        Domoticz.Log("DysonPureLink plugin: onNotification: " + Name + "," + Subject + "," + Text + "," + Status + "," + str(Priority) + "," + Sound + "," + ImageFile)

    def onHeartbeat(self):
        if self.myDevice != None:
            self.runCounter = self.runCounter - 1
            # self.pingCounter = self.pingCounter - 1
            # if self.pingCounter <= 0 and self.runCounter > 0:
                # self.mqttClient.onHeartbeat()
                # self.pingCounter = int(int(Parameters['Mode2'])/2)
            if self.runCounter <= 0:
                Domoticz.Debug("DysonPureLink plugin: Poll unit")
                self.runCounter = int(Parameters['Mode2'])
                #self.pingCounter = int(int(Parameters['Mode2'])/2)
                topic, payload = self.myDevice.request_state()
                self.mqttClient.Publish(topic, payload) #ask for update of current status
                
            else:
                Domoticz.Debug("Polling unit in " + str(self.runCounter) + " heartbeats.")
                self.mqttClient.onHeartbeat()

    def onDeviceRemoved(self, unit):
        Domoticz.Log("DysonPureLink plugin: onDeviceRemoved called for unit '" + str(unit) + "'")
    
    def updateDevices(self):
        """Update the defined devices from incoming mesage info"""
        #update the devices
        if self.state_data.oscillation is not None:
            UpdateDevice(self.fanOscillationUnit, self.state_data.oscillation.state, str(self.state_data.oscillation))
        if self.state_data.night_mode is not None:
            UpdateDevice(self.nightModeUnit, self.state_data.night_mode.state, str(self.state_data.night_mode))

        # Fan speed  
        if self.state_data.fan_speed is not None:
            f_rate = self.state_data.fan_speed
    
            if (f_rate == "AUTO"):
                nValueNew = 110
                sValueNew = "110" # Auto
            else:
                nValueNew = (int(f_rate))*10
                sValueNew = str((int(f_rate)) * 10)
            if self.state_data.fan_mode is not None:
                Domoticz.Debug("update fanspeed, state of FanMode: " + str(self.state_data.fan_mode))
                if self.state_data.fan_mode.state == 0:
                    nValueNew = 0
                    sValueNew = "0"
                    
            UpdateDevice(self.fanSpeedUnit, nValueNew, sValueNew)
        
        if self.state_data.fan_mode is not None:
            UpdateDevice(self.fanModeUnit, self.state_data.fan_mode.state, str((self.state_data.fan_mode.state+1)*10))
        if self.state_data.fan_state is not None:
            UpdateDevice(self.fanStateUnit, self.state_data.fan_state.state, str((self.state_data.fan_state.state+1)*10))
        if self.state_data.filter_life is not None:
            UpdateDevice(self.filterLifeUnit, self.state_data.filter_life, str(self.state_data.filter_life))
        if self.state_data.quality_target is not None:
            UpdateDevice(self.qualityTargetUnit, self.state_data.quality_target.state, str((self.state_data.quality_target.state+1)*10))
        if self.state_data.standby_monitoring is not None:
            UpdateDevice(self.standbyMonitoringUnit, self.state_data.standby_monitoring.state, str((self.state_data.standby_monitoring.state+1)*10))
        if self.state_data.fan_mode_auto is not None:
            UpdateDevice(self.fanModeAutoUnit, self.state_data.fan_mode_auto.state, str((self.state_data.fan_mode_auto.state+1)*10))
        if self.state_data.focus is not None:
            UpdateDevice(self.fanFocusUnit, self.state_data.focus.state, str(self.state_data.focus))
        if self.state_data.heat_mode is not None:
            UpdateDevice(self.heatModeUnit, self.state_data.heat_mode.state, str((self.state_data.heat_mode.state+1)*10))
        if self.state_data.heat_target is not None:
            UpdateDevice(self.heatTargetUnit, 0, str(self.state_data.heat_target))
        if self.state_data.heat_state is not None:
            UpdateDevice(self.heatStateUnit, self.state_data.heat_state.state, str((self.state_data.heat_state.state+1)*10))
        Domoticz.Debug("update StateData: " + str(self.state_data))


    def updateSensors(self):
        """Update the defined devices from incoming mesage info"""
        #update the devices
        if self.sensor_data.temperature is not None and self.sensor_data.humidity is not None :
            tempNum = int(self.sensor_data.temperature)
            humNum = int(self.sensor_data.humidity)
            UpdateDevice(self.tempHumUnit, 1, str(self.sensor_data.temperature)[:4] +';'+ str(self.sensor_data.humidity) + ";1")
        if self.sensor_data.volatile_compounds is not None:
            UpdateDevice(self.volatileUnit, self.sensor_data.volatile_compounds, str(self.sensor_data.volatile_compounds))
        if self.sensor_data.particles is not None:
            UpdateDevice(self.particlesUnit, self.sensor_data.particles, str(self.sensor_data.particles))
        if self.sensor_data.particles2_5 is not None:
            UpdateDevice(self.particles2_5Unit, self.sensor_data.particles2_5, str(self.sensor_data.particles2_5))
        if self.sensor_data.particles10 is not None:
            UpdateDevice(self.particles10Unit, self.sensor_data.particles10, str(self.sensor_data.particles10))
        if self.sensor_data.particulate_matter_25 is not None:
            UpdateDevice(self.particlesMatter25Unit, self.sensor_data.particulate_matter_25, str(self.sensor_data.particulate_matter_25))
        if self.sensor_data.particulate_matter_10 is not None:
            UpdateDevice(self.particlesMatter10Unit, self.sensor_data.particulate_matter_10, str(self.sensor_data.particulate_matter_10))
        if self.sensor_data.nitrogenDioxideDensity is not None:
            UpdateDevice(self.nitrogenDioxideDensityUnit, self.sensor_data.nitrogenDioxideDensity, str(self.sensor_data.nitrogenDioxideDensity))
        if self.sensor_data.heat_target is not None:
            UpdateDevice(self.heatTargetUnit, self.sensor_data.heat_target, str(self.sensor_data.heat_target))
        UpdateDevice(self.sleepTimeUnit, self.sensor_data.sleep_timer, str(self.sensor_data.sleep_timer))
        Domoticz.Debug("update SensorData: " + str(self.sensor_data))
        #Domoticz.Debug("update StateData: " + str(self.state_data))

    def onMQTTConnected(self):
        """connection to device established"""
        Domoticz.Debug("onMQTTConnected called")
        Domoticz.Log("MQTT connection established")
        self.mqttClient.Subscribe([self.base_topic + '/status/current', self.base_topic + '/status/connection', self.base_topic + '/status/faults']) #subscribe to all topics on the machine
        topic, payload = self.myDevice.request_state()
        self.mqttClient.Publish(topic, payload) #ask for update of current status

    def onMQTTDisconnected(self):
        Domoticz.Debug("onMQTTDisconnected")

    def onMQTTSubscribed(self):
        Domoticz.Debug("onMQTTSubscribed")
        
    def onMQTTPublish(self, topic, message):
        Domoticz.Debug("MQTT Publish: MQTT message incoming: " + topic + " " + str(message))

        if (topic == self.base_topic + '/status/current'):
            #update of the machine's status
            if StateData.is_state_data(message):
                Domoticz.Debug("machine state or state change recieved")
                self.state_data = StateData(message)
                self.updateDevices()
            if SensorsData.is_sensors_data(message):
                Domoticz.Debug("sensor state recieved")
                self.sensor_data = SensorsData(message)
                self.updateSensors()

        if (topic == self.base_topic + '/status/connection'):
            #connection status received
            Domoticz.Debug("connection state recieved")

        if (topic == self.base_topic + '/status/software'):
            #connection status received
            Domoticz.Debug("software state recieved")
            
        if (topic == self.base_topic + '/status/summary'):
            #connection status received
            Domoticz.Debug("summary state recieved")

    def checkVersion(self, version):
        """checks actual version against stored version as 'Ma.Mi.Pa' and checks if updates needed"""
        #read version from stored configuration
        ConfVersion = getConfigItem("plugin version", "0.0.0")
        Domoticz.Log("Starting version: " + version )
        MaCurrent,MiCurrent,PaCurrent = version.split('.')
        MaConf,MiConf,PaConf = ConfVersion.split('.')
        Domoticz.Debug("checking versions: current '{0}', config '{1}'".format(version, ConfVersion))
        if int(MaConf) < int(MaCurrent):
            Domoticz.Log("Major version upgrade: {0} -> {1}".format(MaConf,MaCurrent))
            #add code to perform MAJOR upgrades
        elif int(MiConf) < int(MiCurrent):
            Domoticz.Log("Minor version upgrade: {0} -> {1}".format(MiConf,MiCurrent))
            #add code to perform MINOR upgrades
        elif int(PaConf) < int(PaCurrent):
            Domoticz.Log("Patch version upgrade: {0} -> {1}".format(PaConf,PaCurrent))
            #add code to perform PATCH upgrades, if any
        if ConfVersion != version:
            #store new version info
            self._setVersion(MaCurrent,MiCurrent,PaCurrent)
            
    def get_device_names(self):
        """find the amount of stored devices"""
        Configurations = getConfigItem()
        devices = {}
        for x in Configurations:
            if x.find(".") > -1 and x.split(".")[1] == "name":
                devices[str(Configurations[x])] = str(Configurations[x])
        return devices
        
    def get_device_config(self, name):
        """fetch all relevant config items from Domoticz.Configuration for device with name"""
        Configurations = getConfigItem()
        for x in Configurations:
            if x.split(".")[1] == "name":
                Domoticz.Debug("Found a machine name: " + x + " value: '" + str(Configurations[x]) + "'")
                if Configurations[x] == name:
                    password = getConfigItem(Key="{0}.{1}".format(name, "credential"))
                    serialNumber = getConfigItem(Key="{0}.{1}".format(name, "serial"))
                    deviceType = getConfigItem(Key="{0}.{1}".format(name, "product_type"))
                    return password, serialNumber, deviceType
        return
        
    # def _hashed_password(self, pwd):
        # """Hash password (found in manual) to a base64 encoded of its sha512 value"""
        # hash = hashlib.sha512()
        # hash.update(pwd.encode('utf-8'))
        # return base64.b64encode(hash.digest()).decode('utf-8')

    def _setVersion(self, major, minor, patch):
        #set configs
        Domoticz.Debug("Setting version to {0}.{1}.{2}".format(major, minor, patch))
        setConfigItem(Key="MajorVersion", Value=major)
        setConfigItem(Key="MinorVersion", Value=minor)
        setConfigItem(Key="patchVersion", Value=patch)
        setConfigItem(Key="plugin version", Value="{0}.{1}.{2}".format(major, minor, patch))
        
    def _storeCredentials(self, creds, auths):
        #store credentials as config item
        Domoticz.Debug("Storing credentials: " + str(creds) + " and auth object: " + str(auths))
        currentCreds = getConfigItem(Key = "credentials", Default = None)
        if currentCreds is None or currentCreds != creds:
            Domoticz.Log("Credentials from user authentication do not match those stored in config, updating config")
            setConfigItem(Key = "credentials", Value = creds)
        return True
        
# Configuration Helpers
def getConfigItem(Key=None, Default={}):
   Value = Default
   try:
       Config = Domoticz.Configuration()
       if (Key != None):
           Value = Config[Key] # only return requested key if there was one
       else:
           Value = Config      # return the whole configuration if no key
   except KeyError:
       Value = Default
   except Exception as inst:
       Domoticz.Error("Domoticz.Configuration read failed: '"+str(inst)+"'")
   return Value
   
def setConfigItem(Key=None, Value=None):
    Config = {}
    if type(Value) not in (str, int, float, bool, bytes, bytearray, list, dict):
        Domoticz.Error("A value is specified of a not allowed type: '" + str(type(Value)) + "'")
        return Config
    try:
       Config = Domoticz.Configuration()
       if (Key != None):
           Config[Key] = Value
       else:
           Config = Value  # set whole configuration if no key specified
       Config = Domoticz.Configuration(Config)
    except Exception as inst:
       Domoticz.Error("Domoticz.Configuration operation failed: '"+str(inst)+"'")
    return Config
       
def UpdateDevice(Unit, nValue, sValue, BatteryLevel=255, AlwaysUpdate=False):
    if Unit not in Devices: return
    if Devices[Unit].nValue != nValue\
        or Devices[Unit].sValue != sValue\
        or Devices[Unit].BatteryLevel != BatteryLevel\
        or AlwaysUpdate == True:

        Devices[Unit].Update(nValue, str(sValue), BatteryLevel=BatteryLevel)

        Domoticz.Debug("Update %s: nValue %s - sValue %s - BatteryLevel %s" % (
            Devices[Unit].Name,
            nValue,
            sValue,
            BatteryLevel
        ))
        
global _plugin
_plugin = DysonPureLinkPlugin()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onConnect(Connection, Status, Description):
    global _plugin
    _plugin.onConnect(Connection, Status, Description)

def onDisconnect(Connection):
    global _plugin
    _plugin.onDisconnect(Connection)

def onMessage(Connection, Data):
    global _plugin
    _plugin.onMessage(Connection, Data)

def onCommand(Unit, Command, Level, Color):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Color)

def onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile):
    global _plugin
    _plugin.onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile)

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()

def onDeviceRemoved(Unit):
    global _plugin
    _plugin.onDeviceRemoved(Unit)

    # Generic helper functions
def DumpConfigToLog():
    Domoticz.Debug("Parameter count: " + str(len(Parameters)))
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug( "Parameter '" + x + "':'" + str(Parameters[x]) + "'")
    Configurations = getConfigItem()
    Domoticz.Debug("Configuration count: " + str(len(Configurations)))
    for x in Configurations:
        if Configurations[x] != "":
            Domoticz.Debug( "Configuration '" + x + "':'" + str(Configurations[x]) + "'")
    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
    return

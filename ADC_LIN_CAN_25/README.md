ADC Monitoring and LIN Communication with Home Assistant
Features:

    MQTT auto-discovery sensors
    Web UI

Instructions:

    Install the Add-on
    Start the add-on in Home Assistant.

    Open the Web UI
    At this point, you can open the Web UI to monitor the sensors.

    Configure MQTT Sensors:
    To have the MQTT sensors discovered and added to your dashboard, follow these steps:

        Go to Settings > People > Add Person and create a new user.
        You will need this user for MQTT broker configuration.

        Add the MQTT Integration to Home Assistant:
        Go to Settings > Devices & Services.
        In the bottom-right corner, click the Add Integration button.
        From the list, select MQTT.
        Follow the on-screen instructions to complete the setup, using the user you just created.

    Install an MQTT Broker
    To get the MQTT sensors working, you need to install an MQTT broker add-on like Mosquitto.

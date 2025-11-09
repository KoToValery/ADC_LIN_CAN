#!/usr/bin/env python3
import os
import sys
import json
import logging
import time
import signal
import pigpio

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PWMManager:
    def __init__(self, gpio_pin=12, frequency=26000, pigpiod_host="127.0.0.1", pigpiod_port=8888):
        self.gpio_pin = gpio_pin
        self.frequency = frequency
        self.pigpiod_host = pigpiod_host
        self.pigpiod_port = pigpiod_port
        
        self.duty_cycle = 50  # percentage 0-100%
        self.is_enabled = False
        self.is_initialized = False
        self.pi = None
        
        # Connect to pigpiod
        self.connect()
    
    def connect(self):
        """Connect to pigpiod daemon"""
        try:
            logger.info(f"Connecting to pigpiod at {self.pigpiod_host}:{self.pigpiod_port}...")
            self.pi = pigpio.pi(self.pigpiod_host, self.pigpiod_port)
            
            if not self.pi.connected:
                logger.error("Failed to connect to pigpiod daemon!")
                logger.error("Make sure pigpio addon is installed and running.")
                return False
            
            logger.info(f"✓ Connected to pigpiod (version {self.pi.get_pigpio_version()})")
            
            # Set GPIO mode to output
            self.pi.set_mode(self.gpio_pin, pigpio.OUTPUT)
            
            self.is_initialized = True
            logger.info(f"✓ GPIO {self.gpio_pin} initialized for PWM")
            return True
            
        except Exception as e:
            logger.error(f"✗ Error connecting to pigpiod: {e}")
            self.is_initialized = False
            return False
    
    def set_duty_cycle(self, duty_cycle):
        """Set PWM duty cycle (0-100%)"""
        if 0 <= duty_cycle <= 100:
            self.duty_cycle = duty_cycle
            if self.is_enabled and self.is_initialized:
                self._apply_pwm()
            logger.info(f"PWM duty cycle set to {duty_cycle}%")
            return True
        else:
            logger.warning(f"Invalid duty cycle: {duty_cycle}. Must be 0-100%.")
            return False
    
    def set_frequency(self, frequency):
        """Set PWM frequency (Hz)"""
        if 1000 <= frequency <= 100000:
            self.frequency = frequency
            if self.is_enabled and self.is_initialized:
                self._apply_pwm()
            logger.info(f"PWM frequency set to {frequency} Hz")
            return True
        else:
            logger.warning(f"Invalid frequency: {frequency}. Must be 1000-100000 Hz.")
            return False
    
    def _apply_pwm(self):
        """Apply PWM settings to GPIO"""
        try:
            if not self.is_initialized or self.pi is None:
                return
            
            # Calculate duty cycle value (0-1000000 for pigpio)
            # pigpio uses range 0-1000000 for duty cycle
            duty_value = int(self.duty_cycle * 10000)  # 0-100% -> 0-1000000
            
            # Set PWM frequency and duty cycle
            self.pi.set_PWM_frequency(self.gpio_pin, self.frequency)
            self.pi.set_PWM_range(self.gpio_pin, 1000000)
            self.pi.set_PWM_dutycycle(self.gpio_pin, duty_value)
            
            logger.debug(f"Applied PWM: {self.frequency}Hz, {self.duty_cycle}%")
            
        except Exception as e:
            logger.error(f"Error applying PWM: {e}")
    
    def enable_pwm(self):
        """Enable PWM output"""
        if not self.is_initialized:
            logger.error("PWM not initialized")
            return False
        
        try:
            self._apply_pwm()
            self.is_enabled = True
            logger.info(f"✓ Hardware PWM enabled: {self.frequency}Hz @ {self.duty_cycle}%")
            return True
        except Exception as e:
            logger.error(f"Error enabling PWM: {e}")
            return False
    
    def disable_pwm(self):
        """Disable PWM output"""
        if not self.is_initialized or self.pi is None:
            return False
        
        try:
            # Set duty cycle to 0 to turn off
            self.pi.set_PWM_dutycycle(self.gpio_pin, 0)
            self.is_enabled = False
            logger.info("✓ Hardware PWM disabled")
            return True
        except Exception as e:
            logger.error(f"Error disabling PWM: {e}")
            return False
    
    def get_status(self):
        """Get current PWM status"""
        actual_freq = 0
        if self.is_initialized and self.pi is not None:
            try:
                actual_freq = self.pi.get_PWM_frequency(self.gpio_pin)
            except:
                pass
        
        return {
            "enabled": self.is_enabled,
            "duty_cycle": self.duty_cycle,
            "frequency": self.frequency,
            "actual_frequency": actual_freq,
            "gpio_pin": self.gpio_pin,
            "connected": self.is_initialized
        }
    
    def close(self):
        """Cleanup resources"""
        try:
            if self.is_enabled:
                self.disable_pwm()
            
            if self.pi is not None:
                self.pi.stop()
            
            logger.info("PWM Manager closed")
        except Exception as e:
            logger.error(f"Error closing PWM: {e}")


def load_options():
    """Load addon options from Home Assistant"""
    options_path = "/data/options.json"
    default_options = {
        "gpio_pin": 12,
        "duty_cycle": 50,
        "frequency": 26000,
        "auto_start": True,
        "pigpiod_host": "127.0.0.1",
        "pigpiod_port": 8888
    }
    
    if os.path.exists(options_path):
        try:
            with open(options_path, 'r') as f:
                options = json.load(f)
                logger.info(f"Loaded options: {options}")
                return options
        except Exception as e:
            logger.error(f"Error loading options: {e}")
    
    logger.info(f"Using default options: {default_options}")
    return default_options


def main():
    """Main entry point"""
    logger.info("=" * 60)
    logger.info("PWM LED Controller for Home Assistant OS (pigpio)")
    logger.info("=" * 60)
    
    # Load configuration
    options = load_options()
    gpio_pin = options.get("gpio_pin", 12)
    duty_cycle = options.get("duty_cycle", 50)
    frequency = options.get("frequency", 26000)
    auto_start = options.get("auto_start", True)
    pigpiod_host = options.get("pigpiod_host", "127.0.0.1")
    pigpiod_port = options.get("pigpiod_port", 8888)
    
    logger.info(f"Configuration:")
    logger.info(f"  - GPIO Pin: {gpio_pin}")
    logger.info(f"  - Duty Cycle: {duty_cycle}%")
    logger.info(f"  - Frequency: {frequency} Hz")
    logger.info(f"  - Auto Start: {auto_start}")
    logger.info(f"  - pigpiod: {pigpiod_host}:{pigpiod_port}")
    
    # Initialize PWM manager
    pwm = PWMManager(
        gpio_pin=gpio_pin,
        frequency=frequency,
        pigpiod_host=pigpiod_host,
        pigpiod_port=pigpiod_port
    )
    
    if not pwm.is_initialized:
        logger.error("Failed to initialize PWM!")
        logger.error("Make sure the pigpio addon is installed and running.")
        logger.error("Install from: https://github.com/hassio-addons/addon-pigpio")
        sys.exit(1)
    
    # Set duty cycle and frequency
    pwm.set_duty_cycle(duty_cycle)
    pwm.set_frequency(frequency)
    
    # Enable if auto_start
    if auto_start:
        pwm.enable_pwm()
        logger.info(f"✓ PWM started automatically")
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Shutting down...")
        pwm.close()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Keep running and log status periodically
    logger.info("PWM Controller running. Press Ctrl+C to stop.")
    logger.info("-" * 60)
    
    try:
        last_log_time = 0
        while True:
            time.sleep(1)
            
            # Log status every 60 seconds
            current_time = int(time.time())
            if current_time - last_log_time >= 60:
                status = pwm.get_status()
                logger.info(f"Status: {status}")
                last_log_time = current_time
                
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        pwm.close()


if __name__ == "__main__":
    main()

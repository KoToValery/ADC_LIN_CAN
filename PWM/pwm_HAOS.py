#!/usr/bin/env python3
import os
import sys
import json
import logging
import time
import signal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PWMManager:
    def __init__(self, pwm_pin=12, frequency=26000):
        self.pwm_pin = pwm_pin
        self.frequency = frequency
        
        # PWM chip configuration for Pi 5
        self.pwm_chip = "pwmchip0"
        self.pwm_channel = 0  # GPIO12 = PWM0 channel 0
        self.pwm_path = f"/sys/class/pwm/{self.pwm_chip}/pwm{self.pwm_channel}"
        self.period_ns = None
        self.duty_cycle = 50  # percentage 0-100%
        self.is_enabled = False
        self.is_initialized = False
        
        # Initialize PWM
        self.initialize_pwm(self.frequency)
    
    def _write_file(self, path, value):
        """Write value to file"""
        try:
            with open(path, 'w') as f:
                f.write(str(value))
            return True
        except PermissionError:
            logger.error(f"Permission denied writing to {path}")
            return False
        except Exception as e:
            logger.error(f"Error writing to {path}: {e}")
            return False
    
    def _read_file(self, path):
        """Read value from file"""
        try:
            with open(path, 'r') as f:
                return f.read().strip()
        except Exception as e:
            logger.error(f"Error reading {path}: {e}")
            return None
    
    def initialize_pwm(self, frequency: int = 26000):
        """Initialize hardware PWM on GPIO12 at specified frequency"""
        try:
            self.frequency = frequency
            self.period_ns = int(1e9 / frequency)
            
            logger.info(f"Initializing Hardware PWM on GPIO{self.pwm_pin}:")
            logger.info(f"  - Frequency: {frequency} Hz ({frequency/1000} kHz)")
            logger.info(f"  - Period: {self.period_ns} ns")
            
            # Try multiple PWM chips
            for chip_name in ["pwmchip0", "pwmchip2", "pwmchip3"]:
                test_path = f"/sys/class/pwm/{chip_name}"
                if os.path.exists(test_path):
                    self.pwm_chip = chip_name
                    self.pwm_path = f"/sys/class/pwm/{self.pwm_chip}/pwm{self.pwm_channel}"
                    logger.info(f"Found PWM chip: {chip_name}")
                    break
            
            if not os.path.exists(f"/sys/class/pwm/{self.pwm_chip}"):
                logger.error(f"PWM chip not found!")
                logger.error(f"Add to /boot/firmware/config.txt: dtoverlay=pwm,pin={self.pwm_pin},func=4")
                return False
            
            # Export PWM channel if needed
            export_path = f"/sys/class/pwm/{self.pwm_chip}/export"
            if not os.path.exists(self.pwm_path):
                logger.info(f"Exporting PWM channel {self.pwm_channel}...")
                if not self._write_file(export_path, str(self.pwm_channel)):
                    logger.error("Failed to export PWM channel")
                    logger.error("Check permissions: privileged: true, apparmor: false")
                    return False
                time.sleep(0.5)
            
            # Verify export
            if not os.path.exists(self.pwm_path):
                logger.error(f"PWM path {self.pwm_path} does not exist after export")
                return False
            
            # Set period
            period_path = f"{self.pwm_path}/period"
            logger.info(f"Setting period: {self.period_ns} ns")
            if not self._write_file(period_path, str(self.period_ns)):
                return False
            
            # Set duty cycle to 0 initially
            duty_path = f"{self.pwm_path}/duty_cycle"
            if not self._write_file(duty_path, str(0)):
                return False
            
            self.is_initialized = True
            logger.info(f"✓ Hardware PWM initialized successfully ({frequency/1000} kHz)")
            return True
            
        except Exception as e:
            logger.error(f"✗ Error initializing PWM: {e}")
            self.is_initialized = False
            return False
    
    def set_duty_cycle(self, duty_cycle):
        """Set PWM duty cycle (0-100%)"""
        if 0 <= duty_cycle <= 100:
            self.duty_cycle = duty_cycle
            if self.is_enabled and self.is_initialized:
                self._apply_duty_cycle()
            logger.info(f"PWM duty cycle set to {duty_cycle}%")
            return True
        else:
            logger.warning(f"Invalid duty cycle: {duty_cycle}. Must be 0-100%.")
            return False
    
    def _apply_duty_cycle(self):
        """Apply duty cycle immediately"""
        try:
            if self.period_ns is None:
                return
            duty_ns = int(self.period_ns * self.duty_cycle / 100)
            duty_path = f"{self.pwm_path}/duty_cycle"
            self._write_file(duty_path, str(duty_ns))
            logger.debug(f"Applied duty cycle: {duty_ns} ns ({self.duty_cycle}%)")
        except Exception as e:
            logger.error(f"Error applying duty cycle: {e}")
    
    def enable_pwm(self):
        """Enable PWM output"""
        if not self.is_initialized:
            logger.error("PWM not initialized")
            return False
        
        try:
            enable_path = f"{self.pwm_path}/enable"
            self._write_file(enable_path, "1")
            self.is_enabled = True
            self._apply_duty_cycle()
            logger.info(f"✓ Hardware PWM enabled: {self.frequency/1000} kHz @ {self.duty_cycle}%")
            return True
        except Exception as e:
            logger.error(f"Error enabling PWM: {e}")
            return False
    
    def disable_pwm(self):
        """Disable PWM output"""
        if not self.is_initialized:
            return False
        
        try:
            enable_path = f"{self.pwm_path}/enable"
            self._write_file(enable_path, "0")
            self.is_enabled = False
            logger.info("✓ Hardware PWM disabled")
            return True
        except Exception as e:
            logger.error(f"Error disabling PWM: {e}")
            return False
    
    def get_status(self):
        """Get current PWM status"""
        actual_period = None
        actual_duty = None
        
        if self.is_initialized:
            period_path = f"{self.pwm_path}/period"
            duty_path = f"{self.pwm_path}/duty_cycle"
            actual_period = self._read_file(period_path)
            actual_duty = self._read_file(duty_path)
        
        return {
            "enabled": self.is_enabled,
            "duty_cycle": self.duty_cycle,
            "frequency": self.frequency,
            "period_ns": self.period_ns,
            "actual_period_ns": actual_period,
            "actual_duty_ns": actual_duty,
            "gpio_pin": self.pwm_pin,
            "pwm_chip": self.pwm_chip,
            "initialized": self.is_initialized
        }
    
    def close(self):
        """Cleanup resources"""
        try:
            if self.is_enabled:
                self.disable_pwm()
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
        "auto_start": True
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
    logger.info("PWM LED Controller for Home Assistant OS")
    logger.info("Hardware PWM via sysfs (26 kHz capable)")
    logger.info("=" * 60)
    
    # Load configuration
    options = load_options()
    gpio_pin = options.get("gpio_pin", 12)
    duty_cycle = options.get("duty_cycle", 50)
    frequency = options.get("frequency", 26000)
    auto_start = options.get("auto_start", True)
    
    logger.info(f"Configuration:")
    logger.info(f"  - GPIO Pin: {gpio_pin}")
    logger.info(f"  - Duty Cycle: {duty_cycle}%")
    logger.info(f"  - Frequency: {frequency} Hz ({frequency/1000} kHz)")
    logger.info(f"  - Auto Start: {auto_start}")
    
    # Initialize PWM manager
    pwm = PWMManager(pwm_pin=gpio_pin, frequency=frequency)
    
    if not pwm.is_initialized:
        logger.error("Failed to initialize PWM!")
        logger.error("Make sure:")
        logger.error("  1. /boot/firmware/config.txt has: dtoverlay=pwm,pin=12,func=4")
        logger.error("  2. System has been rebooted after config.txt change")
        logger.error("  3. Addon config has: privileged: true, apparmor: false")
        sys.exit(1)
    
    # Set duty cycle
    pwm.set_duty_cycle(duty_cycle)
    
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

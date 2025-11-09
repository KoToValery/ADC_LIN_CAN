#!/usr/bin/env python3
"""
PWM LED Controller Addon за Home Assistant OS
Комуникира с pwm-daemon чрез HTTP REST API
"""
import os
import sys
import json
import logging
import time
import signal
import urllib.request
import urllib.error

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PWMClient:
    """HTTP клиент за pwm-daemon"""
    
    def __init__(self, host="127.0.0.1", port=9000):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.gpio_pin = None
        self.is_initialized = False
    
    def _make_request(self, endpoint, method="GET", data=None):
        """Направи HTTP заявка към daemon"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            if method == "GET":
                with urllib.request.urlopen(url, timeout=5) as response:
                    return json.loads(response.read().decode())
            
            elif method == "POST":
                headers = {'Content-Type': 'application/json'}
                json_data = json.dumps(data).encode() if data else b'{}'
                req = urllib.request.Request(url, data=json_data, headers=headers, method='POST')
                
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.loads(response.read().decode())
        
        except urllib.error.URLError as e:
            logger.error(f"Connection error: {e}")
            return None
        except Exception as e:
            logger.error(f"Request error: {e}")
            return None
    
    def check_connection(self):
        """Провери връзка с daemon"""
        result = self._make_request("/status", "GET")
        if result and result.get("status") == "ok":
            logger.info(f"✓ Connected to pwm-daemon at {self.base_url}")
            return True
        logger.error(f"✗ Cannot connect to pwm-daemon at {self.base_url}")
        return False
    
    def initialize_pwm(self, gpio_pin, frequency):
        """Инициализира PWM"""
        logger.info(f"Initializing PWM on GPIO{gpio_pin} at {frequency}Hz...")
        
        data = {
            "gpio_pin": gpio_pin,
            "frequency": frequency
        }
        
        result = self._make_request("/init", "POST", data)
        if result and result.get("status") == "ok":
            self.gpio_pin = gpio_pin
            self.is_initialized = True
            logger.info(f"✓ PWM initialized: GPIO{gpio_pin}, {frequency}Hz")
            return True
        
        logger.error("✗ Failed to initialize PWM")
        return False
    
    def set_duty_cycle(self, duty_cycle):
        """Настрой duty cycle"""
        if not self.is_initialized:
            logger.error("PWM not initialized")
            return False
        
        data = {
            "gpio_pin": self.gpio_pin,
            "duty_cycle": duty_cycle
        }
        
        result = self._make_request("/duty", "POST", data)
        if result and result.get("status") == "ok":
            logger.info(f"✓ Duty cycle set to {duty_cycle}%")
            return True
        
        logger.error("✗ Failed to set duty cycle")
        return False
    
    def enable_pwm(self):
        """Включи PWM"""
        if not self.is_initialized:
            logger.error("PWM not initialized")
            return False
        
        data = {"gpio_pin": self.gpio_pin}
        
        result = self._make_request("/enable", "POST", data)
        if result and result.get("status") == "ok":
            logger.info("✓ PWM enabled")
            return True
        
        logger.error("✗ Failed to enable PWM")
        return False
    
    def disable_pwm(self):
        """Изключи PWM"""
        if not self.is_initialized:
            return False
        
        data = {"gpio_pin": self.gpio_pin}
        
        result = self._make_request("/disable", "POST", data)
        if result and result.get("status") == "ok":
            logger.info("✓ PWM disabled")
            return True
        
        logger.error("✗ Failed to disable PWM")
        return False
    
    def get_status(self):
        """Вземи статус"""
        if not self.is_initialized:
            return {}
        
        result = self._make_request(f"/status/{self.gpio_pin}", "GET")
        if result and result.get("status") == "ok":
            return result.get("pwm", {})
        
        return {}


def load_options():
    """Load addon options from Home Assistant"""
    options_path = "/data/options.json"
    default_options = {
        "gpio_pin": 12,
        "duty_cycle": 50,
        "frequency": 26000,
        "auto_start": True,
        "daemon_host": "127.0.0.1",
        "daemon_port": 9000
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
    logger.info("HTTP API Client for pwm-daemon")
    logger.info("=" * 60)
    
    # Load configuration
    options = load_options()
    gpio_pin = options.get("gpio_pin", 12)
    duty_cycle = options.get("duty_cycle", 50)
    frequency = options.get("frequency", 26000)
    auto_start = options.get("auto_start", True)
    daemon_host = options.get("daemon_host", "127.0.0.1")
    daemon_port = options.get("daemon_port", 9000)
    
    logger.info(f"Configuration:")
    logger.info(f"  - GPIO Pin: {gpio_pin}")
    logger.info(f"  - Duty Cycle: {duty_cycle}%")
    logger.info(f"  - Frequency: {frequency} Hz ({frequency/1000} kHz)")
    logger.info(f"  - Auto Start: {auto_start}")
    logger.info(f"  - Daemon: {daemon_host}:{daemon_port}")
    
    # Create PWM client
    pwm = PWMClient(host=daemon_host, port=daemon_port)
    
    # Check connection
    if not pwm.check_connection():
        logger.error("Cannot connect to pwm-daemon!")
        logger.error("Make sure pwm-daemon is installed and running on host:")
        logger.error("  sudo systemctl status pwm-daemon")
        sys.exit(1)
    
    # Initialize PWM
    if not pwm.initialize_pwm(gpio_pin, frequency):
        logger.error("Failed to initialize PWM!")
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
        pwm.disable_pwm()
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
                if status:
                    logger.info(f"Status: {status}")
                last_log_time = current_time
                
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        pwm.disable_pwm()


if __name__ == "__main__":
    main()

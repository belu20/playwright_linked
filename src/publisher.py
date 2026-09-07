import os
import datetime
import json
from confluent_kafka import Producer

class DataPublisher:
    def __init__(self, kafka_location: str = None, kafka_topic_post: str = None, local_output_dir: str = None, logger = None):
        self.kafka_location = kafka_location
        self.kafka_topic_post = kafka_topic_post
        self.logger = logger
        
        self.producer = None
        if self.kafka_location:
            try:
                producer_conf = {
                    'bootstrap.servers': self.kafka_location
                }
                self.producer = Producer(producer_conf)
                print(f"[INFO] Kafka Producer initialized for server: {self.kafka_location}")
            except Exception as e:
                print(f"[ERROR] Failed to initialize Kafka Producer: {e}")

    def _delivery_callback(self, err, msg):
        if err is not None:
            print(f"[ERROR] Failed to deliver message: {err}")
            if self.logger:
                self.logger.generate_log(
                    4605,
                    str(err),
                    "Queue",
                    {
                        "client_id": os.environ.get("CLIENT_ID", "1"),
                        "error": str(err)
                    }
                )
        else:
            print(f"[INFO] Message successfully produced to topic {msg.topic()} partition {msg.partition()}")

    def produce_message(self, key: str, value: dict) -> bool:
        # Publish to Kafka
        if self.producer and self.kafka_topic_post:
            try:
                value_bytes = json.dumps(value, default=str).encode('utf-8')
                key_bytes = None if key is None else str(key).encode('utf-8')
                
                self.producer.produce(
                    self.kafka_topic_post,
                    key=key_bytes,
                    value=value_bytes,
                    callback=self._delivery_callback
                )
                self.producer.poll(0)  # Non-blocking trigger delivery callback
                return True
            except Exception as e:
                print(f"[ERROR] Failed to produce message to Kafka: {e}")
                if self.logger:
                    self.logger.generate_log(
                        4605,
                        str(e),
                        "Queue",
                        {
                            "client_id": os.environ.get("CLIENT_ID", "1"),
                            "error": str(e)
                        }
                    )
                return False
        else:
            print("[WARNING] Kafka configuration missing, message not sent.")
            return False

    def flush(self, timeout: float = 2.0):
        """Flush Kafka queue secara aman dengan batas timeout untuk mencegah engine hang."""
        if self.producer:
            try:
                self.producer.flush(timeout)
            except Exception as e:
                print(f"[WARNING] Kafka flush error: {e}")


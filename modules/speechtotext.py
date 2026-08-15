import configparser
from openai import OpenAI
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', filename='artifacts/bot.log', filemode='a')

config = configparser.ConfigParser()
config.read('config.ini')

client = OpenAI(
    api_key=config['OpenAI']['SpeechKey']
)

def voice(filepath):
    """
    Відправляє аудіофайл в OpenAI Whisper і повертає текст.
    """

    with open(filepath, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="whisper-1", 
            file=audio_file,
            language="uk"
        )

    return transcription.text
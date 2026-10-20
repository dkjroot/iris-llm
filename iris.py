"""IRIS: listen, transcribe, ask the model, and say the answer.

The loop is deliberately sequential. Iris finishes speaking before listening
again, which keeps the basic example from answering its own voice.
"""

from __future__ import annotations

import argparse
import importlib
import logging
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import cast

import httpx
from pydantic import BaseModel, Field, ValidationError

from iris_chat_engines.compatible import ChatEngine, ChatError
from iris_chat_engines.IChatEngine import IChatEngine
from iris_ptt.IPTT import IPTT, HoldToTalk
from iris_settings import Settings, load_settings
from iris_sounds.ISounds import ISounds
from iris_stt_engines.ISTT import ISTT
from iris_tts_engines.ITTS import ITTS

ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)


def output_ai_reply(reply: str, tts: ITTS | None = None) -> None:
    print("Iris:", reply)
    if tts is not None:
        tts.speak(reply)


def conversation(
    chat: IChatEngine,
    listen: Callable[[], str | None],
    tts: ITTS | None = None,
    working: Callable[[], None] = lambda: None,
    stop: Callable[[], None] = lambda: None,
) -> None:
    output_ai_reply("Nice to see you again, what's up?", tts)
    while True:
        text = listen()
        if not text or not text.strip():
            continue
        if text.strip().casefold() == "exit":
            output_ai_reply("Shutting down", tts)
            return
        try:
            working()
            reply = chat.get_response(text)
        except ChatError as error:
            print(f"Couldn't get a reply: {error}")
            continue
        finally:
            stop()
        output_ai_reply(reply, tts)


class Feedback:
    """Optional cues should never be the reason you can't have a conversation."""

    def __init__(self, settings: Settings) -> None:
        from iris_sounds.no_sounds import Sounds

        self.sounds: ISounds = Sounds({})
        if settings.sounds:
            try:
                from iris_sounds.beeps_and_bleeps import Sounds as Beeps

                self.sounds = Beeps({})
            except (OSError, ValueError, ImportError) as error:
                LOGGER.warning("Feedback sounds unavailable (%s); continuing quietly", error)

    def play(self, method: str) -> None:
        import sounddevice as sd

        try:
            getattr(self.sounds, method)()
        except (OSError, ValueError, sd.PortAudioError) as error:
            from iris_sounds.no_sounds import Sounds

            LOGGER.warning("Feedback sounds unavailable (%s); continuing quietly", error)
            self.sounds = Sounds({})


def stop_audio() -> None:
    import sounddevice as sd

    sd.stop()


class VoiceInput:
    def __init__(
        self, settings: Settings, stt: ISTT, activation: IPTT | HoldToTalk, feedback: Feedback
    ) -> None:
        import speech_recognition as sr

        self.recognizer = sr.Recognizer()
        self.settings = settings
        self.stt = stt
        self.activation = activation
        self.feedback = feedback

    def listen(self) -> str | None:
        import speech_recognition as sr

        self.feedback.play("play_chirp")
        try:
            if self.settings.activation == "space":
                # Open before announcing readiness. There is no calibration or
                # listening beep after the press to swallow the opening words.
                with sr.Microphone(
                    device_index=self.settings.microphone_index, sample_rate=16000
                ) as source:
                    audio = cast(HoldToTalk, self.activation).record(
                        source, self.settings.phrase_time_limit
                    )
            else:
                # Wake detection owns its mic only during this call.
                if not cast(IPTT, self.activation).wait_to_talk():
                    return None
                self.feedback.play("play_bleep")
                with sr.Microphone(
                    device_index=self.settings.microphone_index, sample_rate=16000
                ) as source:
                    self.recognizer.adjust_for_ambient_noise(source, duration=0.2)
                    print("You: (listening...) ", end="", flush=True)
                    audio = self.recognizer.listen(
                        source,
                        timeout=self.settings.listen_timeout,
                        phrase_time_limit=self.settings.phrase_time_limit,
                    )
            self.feedback.play("play_working_sound")
            text = self.stt.recognize(audio)
            print(text)
            return text
        except (sr.WaitTimeoutError, sr.UnknownValueError):
            print("(didn't catch that)")
            return None
        except sr.RequestError as error:
            raise RuntimeError("Speech recognition failed; check its service/settings") from error
        finally:
            stop_audio()


def make_stt(settings: Settings) -> ISTT:
    module = {
        "whisper": "whisper_offline",
        "wav2vec2": "wav2vec2_offline",
        "google": "google_online",
    }[settings.stt_engine]
    provider = importlib.import_module(f"iris_stt_engines.{module}")
    return cast(ISTT, provider.STT(settings))


def make_activation(settings: Settings, stt: ISTT) -> IPTT | HoldToTalk:
    if settings.activation == "space":
        from iris_ptt.push_space_to_talk import PTT as SpacePTT

        return SpacePTT()
    if settings.activation == "wake":
        from iris_ptt.wake_word import PTT

        return PTT(settings, stt)
    module = {"enter": "press_enter", "continuous": "do_not_wait"}[settings.activation]
    return cast(IPTT, importlib.import_module(f"iris_ptt.{module}").PTT())


def make_tts(settings: Settings) -> ITTS:
    module = {"pyttsx3": "pyttsx3_offline", "kokoro": "kokoro_offline", "gtts": "gTTS_online"}[
        settings.tts_engine
    ]
    return cast(ITTS, importlib.import_module(f"iris_tts_engines.{module}").TTS(settings))


def download_models(settings: Settings) -> None:
    print("Setup download: selected speech model files may be fetched from Hugging Face.")
    if settings.stt_engine == "whisper":
        from iris_stt_engines.whisper_offline import load_model

        load_model(settings, download=True)
    elif settings.stt_engine == "wav2vec2":
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        Wav2Vec2Processor.from_pretrained(settings.tokenizer_model)
        Wav2Vec2ForCTC.from_pretrained(settings.ctc_model)
    else:
        print("Google recognition has no local model to download.")
    if settings.tts_engine == "kokoro":
        from iris_tts_engines.kokoro_offline import download_voices

        download_voices(settings)
    print("Done. Ordinary startup uses local/cached model files only.")


class AvailableModel(BaseModel):
    id: str = Field(min_length=1)


class ModelList(BaseModel):
    data: list[AvailableModel]


def check_setup(settings: Settings, client: httpx.Client) -> int:
    print("Checking the chat server (no conversation or audio is sent)...")
    try:
        response = client.get(
            f"{settings.base_url}/models",
            timeout=10,
            headers=(
                {"Authorization": f"Bearer {settings.api_key.get_secret_value()}"}
                if settings.api_key
                else {}
            ),
        )
        response.raise_for_status()
    except httpx.HTTPError:
        print(
            "Couldn't list models. Check the address/API key and the server console. "
            "Some servers only implement chat; try --text if /models isn't supported."
        )
        return 1
    try:
        models = ModelList.model_validate_json(response.content)
    except ValidationError:
        print(
            "The server answered, but didn't return a standard model list. "
            "Check its console/model name and try --text."
        )
        return 1
    if not models.data:
        print("The server lists no models. Load your chat model in the server first.")
        return 1
    print("Available model IDs (use the appropriate ID in configure.py):")
    for model in models.data:
        print(f"  {model.id}")
    print("Next try --text, then set up the speech models and microphone.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A small local voice conversation with Iris")
    parser.add_argument("--config", type=Path, help="JSON config path")
    parser.add_argument(
        "--text", action="store_true", help="Test chat without loading audio/models"
    )
    parser.add_argument("--check", action="store_true", help="Validate config and check /v1/models")
    parser.add_argument("--trace", action="store_true", help="Print conversation requests/replies")
    parser.add_argument(
        "--download-voices", action="store_true", help="Download/check Kokoro model and voice pack"
    )
    parser.add_argument(
        "--list-voices", action="store_true", help="Show suggested local Kokoro voices"
    )
    parser.add_argument(
        "--test-voice", action="store_true", help="Speak a sample with the selected TTS engine"
    )
    parser.add_argument(
        "--download-models", action="store_true", help="Explicit speech setup download"
    )
    parser.add_argument(
        "--list-microphones", action="store_true", help="List microphone device indices"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    try:
        default = ROOT / "local-config.json"
        config_path = args.config or (default if default.exists() else None)
        if config_path is not None:
            config_path = config_path.resolve()
            print(f"Configuration file: {config_path}")
        else:
            print("Configuration: built-in defaults (no configuration file)")
        settings = load_settings(config_path)
        if args.trace:
            settings.trace = True
        print(f"Chat endpoint: {settings.base_url} | model: {settings.model}")
        print(
            f"Speech: {settings.stt_engine} -> {settings.tts_engine}; "
            f"activation: {settings.activation}"
        )
        if settings.allow_remote:
            print("Remote access is enabled. Check your selected endpoint and speech providers.")
        if settings.trace:
            print("Trace is on: conversation text will be printed here (no trace file is written).")
        if args.list_voices:
            from iris_tts_engines.kokoro_offline import RECOMMENDED_VOICES

            for name, description in RECOMMENDED_VOICES.items():
                print(f"{name}: {description}")
            return 0
        if args.download_voices:
            from iris_tts_engines.kokoro_offline import download_voices

            download_voices(settings)
            return 0
        if args.test_voice:
            tts = make_tts(settings)
            try:
                output_ai_reply(
                    "Hello, I'm Iris. Let's try this voice. The answer is forty-two. "
                    "You can change my voice without changing the language model.",
                    tts,
                )
            finally:
                tts.close()
            return 0
        if args.list_microphones:
            import speech_recognition as sr

            for index, name in enumerate(sr.Microphone.list_microphone_names()):
                print(f"{index}: {name}")
            return 0
        if args.download_models:
            download_models(settings)
            return 0
        # Ignore proxy environment variables and redirects: a local chat stays local.
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            if args.check:
                return check_setup(settings, client)
            chat = ChatEngine(settings, client)
            if args.text:
                conversation(chat, lambda: input("You: "))
            else:
                with ExitStack() as resources:
                    resources.callback(stop_audio)
                    feedback = Feedback(settings)
                    tts = make_tts(settings)
                    resources.callback(tts.close)
                    feedback.play("play_working_sound")
                    try:
                        stt = make_stt(settings)
                        activation = make_activation(settings, stt)
                    finally:
                        stop_audio()
                    voice = VoiceInput(settings, stt, activation, feedback)
                    conversation(
                        chat,
                        voice.listen,
                        tts,
                        lambda: feedback.play("play_working_sound"),
                        stop_audio,
                    )
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nIris: Shutting down")
        return 0
    except (ValidationError, ValueError) as error:
        print(f"Check your configuration: {error}")
        return 1
    except ImportError as error:
        print(
            f"Missing optional dependency ({error.name}). See the installation extras in README.md."
        )
        return 1
    except OSError as error:
        print(f"Couldn't open a required file or audio device: {error}")
        return 1
    except Exception as error:
        # Final application boundary: don't repeatedly reopen a broken device.
        LOGGER.error(
            "Iris couldn't continue (%s). Check audio devices/models and README.md.",
            type(error).__name__,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

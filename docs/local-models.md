# Prepare the local chat model

This is the model chapter used by all three host guides. Do it on the host, not
the Pi. Then return to step 3 of [Windows](setup-windows.md#3-install-iris),
[macOS](setup-macos.md#3-install-iris) or [Ubuntu](setup-linux.md#3-install-iris).

## Pick a file your machine can run

KoboldCPP runs a downloaded GGUF model. GGUF is a file format for weights and their
metadata. The model writes the response; KoboldCPP is the program executing it.
Iris calls KoboldCPP's chat API. You don't need a paid model API key for this local
connection.

For hardware similar to the reference PC, a candidate matching the reported
family is [Google's Gemma 4 26B A4B Q4 GGUF](https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-gguf/tree/main).
Open **Files and versions**, download the chat model GGUF, and keep it in a folder
called `iris-models` in your home directory. If a file is split into numbered
shards, download the complete matching set and select the first shard in a runtime
that supports it. Iris sends text to the chat model; image/audio projection files
are not needed for this pipeline. This candidate is not a claim that it is the
exact quantization used by the reference PC, nor a completed Iris acceptance run.

For a smaller computer, use a smaller instruction/chat GGUF supported by your
KoboldCPP release. Model size is a choice, not an Iris feature requirement. Leave
room for the operating system, model context and speech engines. A 16 GB host is
an initial planning point for smaller quantized models; for the 26B route plan
substantially more memory and check actual loading before proceeding. These are
planning estimates, not measured minimum specifications. Keep at least 30 GB free
for a small setup, and 60 GB or more for large models, Python environments and
recovery copies. Check the actual download sizes before committing disk space.

Read the model's terms and provenance on its publisher's page. Some downloads
need an account or agreement. Once the required files are prepared, the local
conversation route does not need a hosted model subscription.

## Start and check KoboldCPP

1. Open the KoboldCPP program installed in step 2 of your platform guide. In its
   launcher, select the GGUF file. Set the context allocation to **8192** initially;
   use that same value in Iris later.
2. Choose the backend appropriate to the machine: CUDA for a supported NVIDIA
   configuration, Metal on supported Apple Silicon builds, or CPU when you want
   the portable starting point. Vulkan and other GPU routes depend on the build
   and driver. Don't assume every GPU supports every backend.
3. Set the listener to **127.0.0.1**, port **5001**. Leave public tunnels, external
   providers and router mode off for this single local-model route. The Pi will
   connect to Iris, not directly to the model.
   For the Gemma route, enable **Use Jinja** and **Jinja for Tools** in the
   launcher (look in Quick Launch/Context). Jinja renders the model's chat
   template; the second checkbox applies it to tool-bearing requests too.
   Save both in the configuration file. The equivalent command switches are
   `--jinja` and `--jinja_tools`, but don't append them beside `--config` and
   assume they override its saved values.
   [KoboldCPP's flag reference](https://koboldcpp.net/docs/reference/flags/)
   distinguishes these options. The chosen GGUF still needs a compatible template.
4. Launch and wait until the model is loaded. Open `http://127.0.0.1:5001` on the
   host, send a short message in KoboldCPP's interface and wait for an answer.
   If loading fails, resolve its reported memory/backend/model-format error here.
5. Save the working launcher settings to `iris-model.kcpps` in `iris-models` using
   the launcher's save control. Keep the executable and the model at stable paths.
   Later startup examples run `--config` with this file; flags beside `--config`
   may be ignored, so save the listener/context settings in the file itself.

[KoboldCPP's own launcher and option guide](https://github.com/LostRuins/koboldcpp/wiki)
is the reference when labels differ between releases. Record the executable
version. CPU operation may be slow, and a successful short reply doesn't yet
prove tool calling or structured memory output works with the selected model.
If you enable the model's thinking mode, its reasoning also uses the generation
allowance. Iris defaults to `max_tokens: 250`; increase that in advanced
configuration if responses stop before an answer/tool call, keeping the answer
budget and margin inside your allocated context. Test the time tool with a text
client and confirm the actual call before moving on to home devices.

After installing Iris, `python iris.py --check` queries `/v1/models` without
sending a conversation. Copy the advertised model ID into Iris's configuration.
The local base URL is `http://127.0.0.1:5001/v1`. Some hosts lack a model-list route;
that is a diagnostic limitation, not proof that chat itself is broken. Test a
text turn before moving to microphone work.

For home tools, later ask the time and inspect a real device read-back. If the
model merely describes an action, verify tool support and the host's chat/tool
template. Iris sends tool definitions; it doesn't configure KoboldCPP's Jinja
support on your behalf. Alternative hosts are covered in
[server setup](server-setup.md#using-another-model-host-or-adding-an-adapter).

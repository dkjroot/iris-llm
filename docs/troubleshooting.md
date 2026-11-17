# If something doesn't work

- If the connection fails, check the PC's address, the server's listening address,
  the private-network firewall rule and whether the model and Iris processes are
  both still running.
- An HTTP `401` means the client and Iris server have different droid keys.
- An HTTP `422` means the server didn't recognise speech. Try again after the cue.
- An HTTP `502` means Iris couldn't get a model reply. Check its address, model
  name and authentication, then try `python iris.py --check` on the PC.
- An HTTP `503` means speech or conversation storage failed. Check the returned
  error and the PC console. For speech, check installed extras and cached model
  files; for memory, check the database path, available disk space and access.
- If the microphone doesn't work, check permissions and the selected device.
  A Bluetooth speaker doesn't necessarily provide a working microphone too.
- If a model is missing, run `python iris.py --download-models` on the PC while
  online. Startup deliberately doesn't download missing files for you.

Iris doesn't automatically resend a failed network turn. The PC might have
finished it before the connection dropped, so resending could repeat a reply.
Say “clear context” for a fresh conversation if things seem out of step.

[Back to Iris](../README.md) · [All guides](index.md)

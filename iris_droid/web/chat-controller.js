// State and request identity only. DOM, network, storage and audio live in chat.js.
export class ChatController {
  constructor(newSession) {
    this.newSession = newSession;
    this.session = newSession();
    this.voice = null;
    this.privateMode = false;
    this.personality = null;
    this.personalityRevision = 0;
    this.request = null;
    this.closed = false;
  }

  get pending() { return this.request !== null; }

  selectPersonality(name) {
    this.personality = name;
    this.personalityRevision += 1;
  }

  begin() {
    if (this.closed || this.pending) return null;
    this.request = {
      session: this.session,
      personalityRevision: this.personalityRevision,
      abort: new AbortController(),
    };
    return this.request;
  }

  current(request) { return !this.closed && this.request === request; }

  acknowledge(request, reply, privateMode) {
    if (!this.current(request)) return false;
    this.privateMode = privateMode;
    if (reply.selected_personality && request.personalityRevision === this.personalityRevision) {
      this.selectPersonality(reply.selected_personality);
    }
    if (reply.finished) this.session = this.newSession();
    return true;
  }

  finish(request) {
    if (!this.current(request)) return false;
    this.request = null;
    return true;
  }

  close() {
    this.closed = true;
    this.request?.abort.abort();
    this.request = null;
  }
}

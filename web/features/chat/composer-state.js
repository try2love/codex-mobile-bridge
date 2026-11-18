'use strict';

const composerSendModes = Object.freeze(['send', 'queue', 'steer']);
const composerFollowUpModes = Object.freeze(['queue', 'steer']);

// The preference survives idle periods; the effective action always follows the live runtime.
function deriveComposerAction(state, preferred = 'queue') {
  const availableModes = state.availableModes || composerSendModes;
  const followUpMode =
    composerFollowUpModes.includes(preferred) && availableModes.includes(preferred)
      ? preferred
      : composerFollowUpModes.find((candidate) => availableModes.includes(candidate));
  const mode = state.running ? followUpMode || 'queue' : 'send';
  let label = '发送';
  if (state.sending) {
    label = '正在发送…';
  } else if (state.running && mode === 'queue') {
    label = '排队';
  } else if (state.running && mode === 'steer') {
    label = '引导';
  }
  const stop = !!state.running && !state.hasDraft;
  return {
    mode,
    followUpMode,
    stop,
    choices: !!state.running && !!state.hasDraft,
    label,
    sendDisabled:
      !!state.pending ||
      state.sendEnabled === false ||
      !state.hasDraft ||
      stop ||
      !availableModes.includes(mode),
    stopDisabled: !state.running || !!state.pending || state.stopEnabled === false,
    choicesDisabled: !!state.pending || state.modeEnabled === false,
  };
}

if (typeof module !== 'undefined') {
  module.exports = { composerSendModes, composerFollowUpModes, deriveComposerAction };
}

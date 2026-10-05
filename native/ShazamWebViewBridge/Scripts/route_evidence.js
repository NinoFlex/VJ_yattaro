// Only current-route, identity-bound primary track metadata is eligible.
// Missing/changed markup returns the route identity without guessing nearby text.
(() => {
  const fields = window.__vjTrackMetadata;
  return fields ? fields.readPrimaryTrack(location.href) : null;
})();

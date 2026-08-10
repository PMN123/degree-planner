import { useStore } from "../store";

export function TrackAddOn() {
  const majors = useStore((s) => s.majors);
  const tracks = useStore((s) => s.availableTracks);
  const picked = useStore((s) => s.pickedTracks);
  const addTrack = useStore((s) => s.addExtraTrack);
  if (!majors.includes("computer-science-bs") || !tracks.length) return null;
  const chosenNames = tracks.filter((t) => picked.includes(t.id)).map((t) => t.name.replace(" Track", ""));
  return (
    <div className="track-addon">
      <span className="track-addon-label">CS focus</span>
      {chosenNames.map((name) => <span key={name} className="track-chip">{name}</span>)}
      {tracks.filter((t) => !picked.includes(t.id)).map((track) => (
        <button key={track.id} className="track-add-btn" onClick={() => addTrack(track.id)}>+ {track.name.replace(" Track", "")}</button>
      ))}
    </div>
  );
}

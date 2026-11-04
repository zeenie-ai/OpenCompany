/**
 * Dictating into the message box (design handoff chat, "Composer": voice).
 * While it records, a blinking dot, the time and the microphone's level
 * stand where the text was, with Cancel and Use dictation. Use stops the
 * recording, uploads it into the workflow's workspace and has it turned
 * into text (`transcribe_audio`, which deletes the recording); the text goes
 * into the box after what was already written. A recording stops by itself
 * after `MAX_RECORDING_MS`. No microphone (denied, absent) or a failed
 * transcription says so and leaves the box as it was.
 */

import { Check, Loader2, X } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { uploadToWorkspace } from '@/lib/workspaceUpload';
import { cn } from '@/lib/utils';
import { transcribe } from '../data/chatContext';
import type { NotifyTone } from '../host';

/** A recording stops by itself after this long. */
const MAX_RECORDING_MS = 3 * 60 * 1000;
const BARS = 36;
const LEVEL_MS = 110;

function clock(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

export function VoiceRecorder({
  sessionId,
  workflowId,
  compact,
  onText,
  onClose,
  notify,
}: {
  sessionId: string;
  workflowId: string;
  compact: boolean;
  onText: (text: string) => void;
  onClose: () => void;
  notify: (message: string, tone: NotifyTone) => void;
}) {
  const { sendRequest } = useWebSocketActions();
  const [elapsed, setElapsed] = useState(0);
  const [levels, setLevels] = useState<number[]>(() => Array(BARS).fill(4));
  const [busy, setBusy] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const release = useRef<() => void>(() => undefined);
  const finish = useRef<(use: boolean) => void>(() => undefined);

  const stopAll = useCallback(() => {
    release.current();
    release.current = () => undefined;
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    (async () => {
      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch {
        notify('Allow the microphone to dictate.', 'error');
        onClose();
        return;
      }
      if (cancelled) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const type = typeof MediaRecorder.isTypeSupported === 'function' && MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
      const media = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
      media.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.current.push(event.data);
      };
      media.start();
      recorder.current = media;
      let context: AudioContext | null = null;
      let analyser: AnalyserNode | null = null;
      try {
        context = new AudioContext();
        analyser = context.createAnalyser();
        analyser.fftSize = 256;
        context.createMediaStreamSource(stream).connect(analyser);
      } catch {
        analyser = null;
      }
      const samples = analyser ? new Uint8Array(analyser.frequencyBinCount) : null;
      const started = Date.now();
      timer = window.setInterval(() => {
        const now = Date.now() - started;
        setElapsed(now);
        if (analyser && samples) {
          analyser.getByteTimeDomainData(samples);
          let peak = 0;
          for (const sample of samples) peak = Math.max(peak, Math.abs(sample - 128));
          setLevels((current) => [...current.slice(1), 4 + Math.round((peak / 128) * 22)]);
        }
        if (now >= MAX_RECORDING_MS) finish.current(true);
      }, LEVEL_MS);
      release.current = () => {
        window.clearInterval(timer);
        if (media.state !== 'inactive') media.stop();
        stream.getTracks().forEach((track) => track.stop());
        void context?.close().catch(() => undefined);
      };
    })();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      release.current();
    };
  }, [notify, onClose]);

  // Read through a ref, so the recording's own timer (auto-stop) and the
  // buttons reach the current one.
  useLayoutEffect(() => {
    finish.current = (use: boolean) => {
      const media = recorder.current;
      if (!use || !media) {
        stopAll();
        onClose();
        return;
      }
      if (busy) return;
      setBusy(true);
      media.onstop = async () => {
        const type = media.mimeType || 'audio/webm';
        const file = new File(chunks.current, `dictation-${Date.now()}.${type.includes('ogg') ? 'ogg' : 'webm'}`, { type });
        try {
          const ref = await uploadToWorkspace(file, workflowId);
          const text = (await transcribe(sendRequest, sessionId, ref.path)).trim();
          if (text) onText(text);
          else notify('Nothing was heard. Try again closer to the microphone.', 'info');
        } catch {
          notify('Couldn’t turn that into text. Try again.', 'error');
        } finally {
          onClose();
        }
      };
      stopAll();
    };
  });

  return (
    <div className={cn('flex items-center gap-3', compact ? 'h-8' : 'h-10.25')}>
      {busy ? (
        <span role="status" className="flex items-center gap-2 text-sm text-fg-muted">
          <Loader2 aria-hidden className="size-3.5 animate-spin" />
          Turning it into text…
        </span>
      ) : (
        <>
          <span aria-hidden className="size-2 shrink-0 rounded-full bg-action-stop-ink motion-safe:animate-[opencompany-pip-blink_1.2s_infinite]" />
          <span className="min-w-9.5 font-mono text-sm text-fg-default" aria-label="Recording time">
            {clock(elapsed)}
          </span>
          <div aria-hidden className="flex h-7 min-w-0 flex-1 items-center gap-0.75 overflow-hidden">
            {levels.map((height, index) => (
              <span
                key={index}
                className="max-w-1 min-w-0.5 flex-1 rounded-sm bg-fg-muted transition-[height] duration-(--dur-fast)"
                style={{ height }}
              />
            ))}
          </div>
        </>
      )}
      <Button variant="quiet" size="icon" disabled={busy} onClick={() => finish.current(false)} aria-label="Cancel dictation" title="Cancel" className="shrink-0 rounded-full">
        <X aria-hidden className="size-3.75" strokeWidth={2.2} />
      </Button>
      <Button
        variant="invert"
        size="icon"
        disabled={busy}
        onClick={() => finish.current(true)}
        aria-label="Use dictation"
        title="Use dictation"
        className="shrink-0 rounded-full"
      >
        <Check aria-hidden className="size-3.75" strokeWidth={2.4} />
      </Button>
    </div>
  );
}

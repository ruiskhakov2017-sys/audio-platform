'use client';

import { useEffect, useRef } from 'react';
import { usePlayerStore } from '@/store/playerStore';
import { useHistoryStore } from '@/store/historyStore';
import {
  trackAudioPlayStart,
  trackAudioMilestone,
  trackAudioComplete,
} from '@/lib/analytics';
import { ALL_STORIES_FREE_TO_LISTEN } from '@/config/access';
import type { Story } from '@/types/story';

const SAVE_INTERVAL_MS = 6000;
const MILESTONES = [25, 50, 75, 100] as const;

const useAudioEngine = () => {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const lastSavedRef = useRef(0);
  const trackRef = useRef<Story | null>(null);
  const playStartSentForTrackId = useRef<number | null>(null);
  const milestonesSent = useRef<Set<string>>(new Set());
  const audioResolveFailedRef = useRef<Set<string>>(new Set());

  const currentTrack = usePlayerStore((state) => state.currentTrack);
  const isPlaying = usePlayerStore((state) => state.isPlaying);
  const volume = usePlayerStore((state) => state.volume);
  const playbackRate = usePlayerStore((state) => state.playbackRate);
  const setPosition = usePlayerStore((state) => state.setPosition);
  const setDuration = usePlayerStore((state) => state.setDuration);
  const pause = usePlayerStore((state) => state.pause);
  const seekTarget = usePlayerStore((state) => state.seekTarget);
  const setSeekTarget = usePlayerStore((state) => state.setSeekTarget);
  const addToHistory = useHistoryStore((state) => state.addToHistory);

  useEffect(() => {
    if (currentTrack && isPlaying) {
      addToHistory(currentTrack);
    }
  }, [currentTrack?.id, isPlaying, addToHistory]);

  useEffect(() => {
    trackRef.current = currentTrack;
  }, [currentTrack]);

  useEffect(() => {
    playStartSentForTrackId.current = null;
    milestonesSent.current.clear();
    audioResolveFailedRef.current.clear();
  }, [currentTrack?.id]);

  const next = usePlayerStore((state) => state.next);
  const isAutoPlay = usePlayerStore((state) => state.isAutoPlay);
  const checkSleepTimer = usePlayerStore((state) => state.checkSleepTimer);

  useEffect(() => {
    // Check sleep timer every second
    const interval = setInterval(() => {
      checkSleepTimer();
    }, 1000);
    return () => clearInterval(interval);
  }, [checkSleepTimer]);

  /** Режим «все бесплатно»: при пустом audio_url из view — догружаем URL с /api/story-audio (нужен SUPABASE_SERVICE_ROLE_KEY на сервере). */
  useEffect(() => {
    if (!ALL_STORIES_FREE_TO_LISTEN || !currentTrack) return;
    if (currentTrack.audioSrc?.trim()) return;

    const key = String(currentTrack.rawId ?? currentTrack.id);
    if (audioResolveFailedRef.current.has(key)) return;

    let cancelled = false;
    fetch(`/api/story-audio?id=${encodeURIComponent(key)}`)
      .then((res) => {
        if (!res.ok) throw new Error(String(res.status));
        return res.json() as Promise<{ audio_url?: string }>;
      })
      .then((data) => {
        if (cancelled) return;
        const url = data.audio_url?.trim();
        if (!url) throw new Error('empty');
        usePlayerStore.getState().patchTrackAudioSrc(currentTrack.id, url);
      })
      .catch(() => {
        if (!cancelled) audioResolveFailedRef.current.add(key);
      });

    return () => {
      cancelled = true;
    };
  }, [currentTrack?.id, currentTrack?.rawId, currentTrack?.audioSrc]);

  useEffect(() => {
    if (!audioRef.current) {
      audioRef.current = new Audio();
    }

    const audio = audioRef.current;

    const handleTimeUpdate = () => {
      setPosition(audio.currentTime || 0);
      if (isFinite(audio.duration)) {
        setDuration(audio.duration);
      }

      const track = trackRef.current;
      if (!track) return;

      const dur = audio.duration;
      const pos = audio.currentTime || 0;
      if (isFinite(dur) && dur > 0) {
        const pct = (pos / dur) * 100;
        for (const m of MILESTONES) {
          if (pct >= m - 0.5) {
            const key = `${track.id}-${m}`;
            if (!milestonesSent.current.has(key)) {
              milestonesSent.current.add(key);
              trackAudioMilestone({
                storyId: track.id,
                percent: m,
                positionSec: pos,
                durationSec: dur,
              });
            }
          }
        }
      }

      const now = Date.now();
      if (now - lastSavedRef.current > SAVE_INTERVAL_MS) {
        lastSavedRef.current = now;
        try {
          if (typeof window !== "undefined" && window.localStorage) {
            localStorage.setItem(`progress:${track.id}`, Math.floor(audio.currentTime).toString());
          }
        } catch (_) { }
      }
    };

    const handleLoaded = () => {
      if (isFinite(audio.duration)) {
        setDuration(audio.duration);
      }
    };

    const handleEnded = () => {
      const endedTrack = trackRef.current;
      if (endedTrack) {
        trackAudioComplete({ storyId: endedTrack.id, title: endedTrack.title });
      }
      if (isAutoPlay) {
        next();
      } else {
        pause();
      }
    };

    audio.addEventListener('timeupdate', handleTimeUpdate);
    audio.addEventListener('loadedmetadata', handleLoaded);
    audio.addEventListener('ended', handleEnded);

    return () => {
      audio.removeEventListener('timeupdate', handleTimeUpdate);
      audio.removeEventListener('loadedmetadata', handleLoaded);
      audio.removeEventListener('ended', handleEnded);
    };
  }, [pause, setDuration, setPosition, next, isAutoPlay]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    if (!currentTrack) {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      setPosition(0);
      setDuration(0);
      if (isPlaying) pause();
      return;
    }

    if (!currentTrack.audioSrc?.trim()) {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      setPosition(0);
      setDuration(0);
      const waitForFreeResolve = ALL_STORIES_FREE_TO_LISTEN;
      if (isPlaying && !waitForFreeResolve) pause();
      return;
    }

    // Check if the source URL has actually changed.
    // audio.src is always absolute. currentTrack.audioSrc might be relative.
    // We create a temporary anchor to resolve the relative URL for comparison.
    const tempAnchor = document.createElement('a');
    tempAnchor.href = currentTrack.audioSrc;
    const resolvedSrc = tempAnchor.href;

    if (audio.src !== resolvedSrc) {
      audio.src = resolvedSrc;
      audio.load();
      audio.currentTime = 0;
      setPosition(0);
      setDuration(currentTrack.durationSec || 0);
    }
  }, [currentTrack, currentTrack?.audioSrc, isPlaying, pause, setDuration, setPosition]); // Only depend on audioSrc, not the whole track object

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.volume = Math.max(0, Math.min(1, volume));
  }, [volume]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.playbackRate = playbackRate;
  }, [playbackRate]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !currentTrack?.audioSrc) return;
    if (isPlaying) {
      audio
        .play()
        .then(() => {
          const t = trackRef.current;
          if (!t?.audioSrc?.trim()) return;
          if (playStartSentForTrackId.current === t.id) return;
          playStartSentForTrackId.current = t.id;
          trackAudioPlayStart({
            storyId: t.id,
            title: t.title,
            isPremium: t.isPremium,
            durationSec: t.durationSec,
          });
        })
        .catch(() => null);
    } else {
      audio.pause();
    }
  }, [currentTrack?.audioSrc, isPlaying]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || seekTarget == null) return;
    const t = Math.max(0, seekTarget);
    audio.currentTime = t;
    setPosition(t);
    setSeekTarget(null);
  }, [seekTarget, setPosition, setSeekTarget]);
};

export default useAudioEngine;

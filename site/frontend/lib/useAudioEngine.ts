'use client';

import { useEffect, useRef } from 'react';
import { usePlayerStore } from '@/store/playerStore';
import { useHistoryStore } from '@/store/historyStore';
import type { Story } from '@/types/story';

const SAVE_INTERVAL_MS = 6000;

const useAudioEngine = () => {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const lastSavedRef = useRef(0);
  const trackRef = useRef<Story | null>(null);

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

      const now = Date.now();
      if (now - lastSavedRef.current > SAVE_INTERVAL_MS) {
        lastSavedRef.current = now;
        try {
          localStorage.setItem(`progress:${track.id}`, Math.floor(audio.currentTime).toString());
        } catch {}
      }
    };

    const handleLoaded = () => {
      if (isFinite(audio.duration)) {
        setDuration(audio.duration);
      }
    };

    const handleEnded = () => {
      pause();
    };

    audio.addEventListener('timeupdate', handleTimeUpdate);
    audio.addEventListener('loadedmetadata', handleLoaded);
    audio.addEventListener('ended', handleEnded);

    return () => {
      audio.removeEventListener('timeupdate', handleTimeUpdate);
      audio.removeEventListener('loadedmetadata', handleLoaded);
      audio.removeEventListener('ended', handleEnded);
    };
  }, [pause, setDuration, setPosition]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    if (!currentTrack || !currentTrack.audioSrc) {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      setPosition(0);
      setDuration(0);
      if (isPlaying) pause();
      return;
    }

    if (audio.src !== currentTrack.audioSrc) {
      audio.src = currentTrack.audioSrc;
      audio.load();
      audio.currentTime = 0;
      setPosition(0);
      setDuration(currentTrack.durationSec || 0);
    }
  }, [currentTrack, isPlaying, pause, setDuration, setPosition]);

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
      audio.play().catch(() => null);
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

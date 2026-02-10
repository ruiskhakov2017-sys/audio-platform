'use client';

import { useState, useCallback, useEffect, useRef } from 'react';

type UseAudioPlayerOptions = {
  src: string | null;
};

const formatTime = (sec: number): string => {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
};

export function useAudioPlayer({ src }: UseAudioPlayerOptions) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const isClient = typeof window !== 'undefined';

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

  const togglePlay = useCallback(() => {
    if (!audioRef.current) return;
    if (isPlaying) {
      audioRef.current.pause();
    } else {
      audioRef.current.play().catch(() => setIsPlaying(false));
    }
  }, [isPlaying]);

  const jump = useCallback((seconds: number) => {
    if (!audioRef.current) return;
    const next = Math.max(0, Math.min(audioRef.current.duration, audioRef.current.currentTime + seconds));
    audioRef.current.currentTime = next;
    setCurrentTime(next);
  }, []);

  const seek = useCallback((time: number) => {
    if (!audioRef.current) return;
    const clamped = Math.max(0, Math.min(audioRef.current.duration || 0, time));
    audioRef.current.currentTime = clamped;
    setCurrentTime(clamped);
  }, []);

  useEffect(() => {
    if (!isClient) return;
    const audio = new Audio();
    audioRef.current = audio;

    const handleTimeUpdate = () => setCurrentTime(audio.currentTime);
    const handleLoadedMetadata = () => setDuration(audio.duration);
    const handleEnded = () => {
      setIsPlaying(false);
      setCurrentTime(0);
    };
    const handlePlay = () => setIsPlaying(true);
    const handlePause = () => setIsPlaying(false);

    audio.addEventListener('timeupdate', handleTimeUpdate);
    audio.addEventListener('loadedmetadata', handleLoadedMetadata);
    audio.addEventListener('ended', handleEnded);
    audio.addEventListener('play', handlePlay);
    audio.addEventListener('pause', handlePause);

    return () => {
      audio.pause();
      audio.removeEventListener('timeupdate', handleTimeUpdate);
      audio.removeEventListener('loadedmetadata', handleLoadedMetadata);
      audio.removeEventListener('ended', handleEnded);
      audio.removeEventListener('play', handlePlay);
      audio.removeEventListener('pause', handlePause);
      audio.src = '';
      audioRef.current = null;
    };
  }, [isClient]);

  useEffect(() => {
    if (!isClient || !audioRef.current || !src) return;
    const audio = audioRef.current;
    audio.src = src;
    audio.load();
    setCurrentTime(0);
    setDuration(0);
    setIsPlaying(false);
  }, [src, isClient]);

  return {
    isPlaying,
    currentTime,
    duration,
    progress,
    togglePlay,
    jump,
    seek,
    formatTime,
  };
}

import { create } from 'zustand';
import type { Story } from '@/types/story';

type PlayerState = {
  currentTrack: Story | null;
  queue: Story[];
  isPlaying: boolean;
  isExpanded: boolean;
  position: number;
  duration: number;
  volume: number;
  playbackRate: number;
  play: (story: Story, queue?: Story[]) => void;
  setTrack: (story: Story) => void;
  setIsPlaying: (playing: boolean) => void;
  pause: () => void;
  resume: () => void;
  toggleExpand: () => void;
  setQueue: (queue: Story[]) => void;
  setPosition: (position: number) => void;
  setDuration: (duration: number) => void;
  setVolume: (volume: number) => void;
  setPlaybackRate: (rate: number) => void;
  next: () => void;
  previous: () => void;
  seekTarget: number | null;
  setSeekTarget: (v: number | null) => void;
  seek: (position: number) => void;
};

export const usePlayerStore = create<PlayerState>((set, get) => ({
  currentTrack: null,
  queue: [],
  isPlaying: false,
  isExpanded: false,
  position: 0,
  duration: 0,
  volume: 0.8,
  playbackRate: 1,
  play: (story, queue = []) =>
    set({
      currentTrack: story,
      queue: queue.length ? queue : get().queue,
      isPlaying: true,
      position: 0,
      duration: story.durationSec || 0,
    }),
  setTrack: (story) =>
    set({
      currentTrack: story,
      isPlaying: true,
      position: 0,
      duration: story.durationSec || 0,
    }),
  setIsPlaying: (playing) => set({ isPlaying: playing }),
  pause: () => set({ isPlaying: false }),
  resume: () => set({ isPlaying: true }),
  toggleExpand: () => set((state) => ({ isExpanded: !state.isExpanded })),
  setQueue: (queue) => set({ queue }),
  setPosition: (position) => set({ position }),
  setDuration: (duration) => set({ duration }),
  setVolume: (volume) => set({ volume }),
  setPlaybackRate: (rate) => set({ playbackRate: rate }),
  next: () => {
    const { queue, currentTrack } = get();
    if (!currentTrack || queue.length === 0) return;
    const currentIndex = queue.findIndex((story) => story.slug === currentTrack.slug);
    const nextIndex = currentIndex >= 0 ? (currentIndex + 1) % queue.length : 0;
    set({ currentTrack: queue[nextIndex], isPlaying: true, position: 0 });
  },
  previous: () => {
    const { queue, currentTrack } = get();
    if (!currentTrack || queue.length === 0) return;
    const currentIndex = queue.findIndex((story) => story.slug === currentTrack.slug);
    const prevIndex = currentIndex > 0 ? currentIndex - 1 : queue.length - 1;
    set({ currentTrack: queue[prevIndex], isPlaying: true, position: 0 });
  },
  seekTarget: null,
  setSeekTarget: (v) => set({ seekTarget: v }),
  seek: (position) => set({ seekTarget: position }),
}));

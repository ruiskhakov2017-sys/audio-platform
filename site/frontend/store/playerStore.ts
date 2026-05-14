import { create } from 'zustand';
import { ALL_STORIES_FREE_TO_LISTEN } from '@/config/access';
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
  isPremiumUser: boolean;
  isPaywallOpen: boolean;
  setPremiumStatus: (status: boolean) => void;
  setPaywallOpen: (isOpen: boolean) => void;
  play: (story: Story, queue?: Story[]) => void;
  setTrack: (story: Story) => void;
  setStory: (story: Story) => void;
  setIsPlaying: (playing: boolean) => void;
  togglePlay: () => void;
  pause: () => void;
  resume: () => void;
  toggleExpand: () => void;
  setQueue: (queue: Story[]) => void;
  setPosition: (position: number) => void;
  setDuration: (duration: number) => void;
  setVolume: (volume: number) => void;
  setPlaybackRate: (rate: number) => void;
  isAutoPlay: boolean;
  sleepTimer: number | null; // timestamp when to stop
  setAutoPlay: (enabled: boolean) => void;
  setSleepTimer: (minutes: number | null) => void;
  toggleAutoPlay: () => void;
  checkSleepTimer: () => boolean; // returns true if should stop
  
  next: () => void;
  previous: () => void;
  seekTarget: number | null;
  setSeekTarget: (v: number | null) => void;
  seek: (position: number) => void;
  /** Подставить реальный audio_url (режим «все бесплатно» + догрузка с API). */
  patchTrackAudioSrc: (storyId: string | number, audioSrc: string) => void;
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
  isPremiumUser: false,
  isPaywallOpen: false,
  isAutoPlay: false,
  sleepTimer: null,

  setPremiumStatus: (status) => set({ isPremiumUser: status }),
  setPaywallOpen: (isOpen) => set({ isPaywallOpen: isOpen }),
  setAutoPlay: (enabled) => set({ isAutoPlay: enabled }),
  toggleAutoPlay: () => set((state) => ({ isAutoPlay: !state.isAutoPlay })),
  
  setSleepTimer: (minutes) => {
    if (minutes === null) {
      set({ sleepTimer: null });
      return;
    }
    const targetTime = Date.now() + minutes * 60 * 1000;
    set({ sleepTimer: targetTime });
  },

  checkSleepTimer: () => {
    const { sleepTimer, isPlaying } = get();
    if (sleepTimer && Date.now() >= sleepTimer) {
      if (isPlaying) {
        set({ isPlaying: false, sleepTimer: null });
        return true;
      }
      set({ sleepTimer: null });
    }
    return false;
  },

  play: (story, queue = []) => {
    if (!ALL_STORIES_FREE_TO_LISTEN && story.isPremium && !get().isPremiumUser) {
      set({ isPaywallOpen: true });
      return;
    }
    set({
      currentTrack: story,
      queue: queue.length ? queue : get().queue,
      isPlaying: true,
      position: 0,
      duration: story.durationSec || 0,
    });
  },
  setTrack: (story) => {
    if (!ALL_STORIES_FREE_TO_LISTEN && story.isPremium && !get().isPremiumUser) {
      set({ isPaywallOpen: true });
      return;
    }
    set({
      currentTrack: story,
      isPlaying: true,
      position: 0,
      duration: story.durationSec || 0,
    });
  },
  setStory: (story) => {
    if (!ALL_STORIES_FREE_TO_LISTEN && story.isPremium && !get().isPremiumUser) {
      set({ isPaywallOpen: true });
      return;
    }
    set({
      currentTrack: story,
      isPlaying: true,
      position: 0,
      duration: story.durationSec || 0,
    });
  },
  setIsPlaying: (playing) => set({ isPlaying: playing }),
  togglePlay: () => set((state) => ({ isPlaying: !state.isPlaying })),
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
    const currentIndex = queue.findIndex(
      (s) => String(s.id) === String(currentTrack.id)
    );
    const nextIndex = currentIndex >= 0 ? (currentIndex + 1) % queue.length : 0;
    set({ currentTrack: queue[nextIndex], isPlaying: true, position: 0 });
  },
  previous: () => {
    const { queue, currentTrack } = get();
    if (!currentTrack || queue.length === 0) return;
    const currentIndex = queue.findIndex(
      (s) => String(s.id) === String(currentTrack.id)
    );
    const prevIndex = currentIndex > 0 ? currentIndex - 1 : queue.length - 1;
    set({ currentTrack: queue[prevIndex], isPlaying: true, position: 0 });
  },
  seekTarget: null,
  setSeekTarget: (v) => set({ seekTarget: v }),
  seek: (position) => set({ seekTarget: position }),
  patchTrackAudioSrc: (storyId, audioSrc) => {
    const sid = String(storyId);
    const { currentTrack, queue } = get();
    const patch = (s: Story | null): Story | null => {
      if (!s || String(s.id) !== sid) return s;
      return { ...s, audioSrc };
    };
    set({
      currentTrack: patch(currentTrack),
      queue: queue.map((s) => (String(s.id) === sid ? { ...s, audioSrc } : s)),
    });
  },
}));

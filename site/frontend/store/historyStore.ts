import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { Story } from '@/types/story';

const MAX_HISTORY = 10;

type HistoryState = {
  history: Story[];
  addToHistory: (story: Story) => void;
  clearHistory: () => void;
};

export const useHistoryStore = create<HistoryState>()(
  persist(
    (set) => ({
      history: [],
      addToHistory: (story) =>
        set((state) => {
          const rest = state.history.filter((s) => s.id !== story.id);
          return {
            history: [story, ...rest].slice(0, MAX_HISTORY),
          };
        }),
      clearHistory: () => set({ history: [] }),
    }),
    { name: 'history-storage' }
  )
);

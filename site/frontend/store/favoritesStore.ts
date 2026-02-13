import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type FavoritesState = {
  likedIds: string[];
  toggleLike: (id: string) => void;
  setLikedIds: (ids: string[]) => void;
  isLiked: (id: string) => boolean;
};

export const useFavoritesStore = create<FavoritesState>()(
  persist(
    (set, get) => ({
      likedIds: [],
      toggleLike: (id) =>
        set((state) => ({
          likedIds: state.likedIds.includes(id)
            ? state.likedIds.filter((x) => x !== id)
            : [...state.likedIds, id],
        })),
      setLikedIds: (ids) => set({ likedIds: ids }),
      isLiked: (id) => get().likedIds.includes(id),
    }),
    { name: 'favorites-storage' }
  )
);

'use client';

import { motion } from 'framer-motion';
import { Play, Heart, Lock } from 'lucide-react';
import Image from 'next/image';
import Link from 'next/link';
import { useState } from 'react';
import { useFavoritesStore } from '@/store/favoritesStore';
import { usePlayerStore } from '@/store/playerStore';
import { toggleFavoriteApi } from '@/lib/favoritesApi';
import type { Story } from '@/types/story';

interface StoryTileProps {
    id: number;
    title: string;
    coverImage: string;
    category?: string;
    price?: number;
    isPremium: boolean;
    story?: Story;
}

export function StoryTile({ id, title, coverImage, price, isPremium, story }: StoryTileProps) {
    const [isHovered, setIsHovered] = useState(false);
    const isFree = !isPremium && (price === undefined || price === 0);
    const { toggleLike, isLiked } = useFavoritesStore();
    const { setTrack, setIsPlaying } = usePlayerStore();
    const liked = isLiked(String(id));

    const handleLikeClick = async (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        const slug = story?.slug;
        const useApi = Boolean(slug && process.env.NEXT_PUBLIC_API_URL && typeof window !== 'undefined' && localStorage.getItem('auth_access_token'));
        if (useApi) {
            const res = await toggleFavoriteApi(slug!);
            if (res) toggleLike(String(id));
        } else {
            toggleLike(String(id));
        }
    };

    const handlePlayClick = (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (story) {
            setTrack(story);
            setIsPlaying(true);
        }
    };

    const displayTags = story?.tags || [];
    const storyHref = `/story/${id}`;

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
            onMouseEnter={() => setIsHovered(true)}
            onMouseLeave={() => setIsHovered(false)}
        >
            <Link href={storyHref}>
                <div className="relative aspect-[3/4] rounded-xl overflow-hidden group cursor-pointer">
                    <div className="absolute inset-0">
                        <Image
                            src={coverImage}
                            alt={title}
                            fill
                            className="object-cover transition-transform duration-500 group-hover:scale-110"
                            unoptimized
                            sizes="(max-width: 768px) 100vw, (max-width: 1024px) 50vw, 25vw"
                        />
                    </div>

                    {/* Like — top-left */}
                    <button
                        type="button"
                        onClick={handleLikeClick}
                        className="absolute top-3 left-3 z-10 p-2 rounded-full bg-black/40 hover:bg-black/60 transition-colors"
                        aria-label={liked ? 'Убрать из избранного' : 'В избранное'}
                    >
                        <Heart
                            className={`w-5 h-5 ${liked ? 'fill-cyan-500 text-cyan-500' : 'text-white'}`}
                            strokeWidth={1.5}
                        />
                    </button>
                    {/* Free / Premium (замок) — top-right corner */}
                    <div className="absolute top-3 right-3 z-10">
                        {isFree ? (
                            <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-teal-500/20 text-teal-300 border border-teal-500/50">
                                Free
                            </span>
                        ) : (
                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-500/20 text-amber-300 border border-amber-500/50">
                                <Lock className="w-3.5 h-3.5 shrink-0" strokeWidth={2} />
                                По подписке
                            </span>
                        )}
                    </div>

                    <div className="absolute inset-0 bg-gradient-to-t from-black via-black/60 to-transparent" />

                    {isHovered && (
                        <motion.div
                            className="absolute inset-0 flex items-center justify-center bg-black/30"
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                        >
                            <motion.button
                                type="button"
                                onClick={handlePlayClick}
                                className="w-16 h-16 rounded-full bg-[#00B4D8] flex items-center justify-center shadow-[0_0_20px_rgba(0,180,216,0.6)] border-0 cursor-pointer hover:scale-110 transition-transform mb-3"
                                initial={{ scale: 0.8 }}
                                animate={{ scale: 1 }}
                                whileHover={{ scale: 1.1 }}
                                aria-label="Слушать"
                            >
                                <Play className="w-8 h-8 text-white ml-1" strokeWidth={2.5} fill="white" />
                            </motion.button>
                        </motion.div>
                    )}

                    <div className="absolute bottom-0 left-0 right-0 p-4 pb-4 flex flex-col gap-2 z-20">
                        <h3 className="text-xl font-bold text-white leading-tight drop-shadow-sm truncate text-center block">
                            {title}
                        </h3>
                        <div className="relative w-full overflow-hidden">
                            <div className="flex flex-nowrap w-max gap-2 will-change-transform group-hover:[animation:scroll_12s_linear_infinite]">
                                {[...displayTags, ...displayTags].map((tag, index) => (
                                    <span
                                        key={`scroll-${tag}-${index}`}
                                        className="text-[10px] font-bold uppercase tracking-wider text-[#00B4D8] bg-[#00B4D8]/10 px-2 py-0.5 rounded shadow-[0_0_8px_rgba(0,180,216,0.1)] whitespace-nowrap inline-block"
                                    >
                                        {tag}
                                    </span>
                                ))}
                            </div>
                        </div>
                    </div>
                </div>
            </Link>
        </motion.div>
    );
}

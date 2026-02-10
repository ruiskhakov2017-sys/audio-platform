'use client';

import { motion, AnimatePresence } from 'framer-motion';
import { Play, Pause, SkipBack, SkipForward, Volume2, X } from 'lucide-react';
import { useState } from 'react';
import { NeonWaveform } from '../ui/NeonWaveform';
import Image from 'next/image';

interface PlayerBarProps {
    // In a real app, this would come from global state (zustand)
    isVisible?: boolean;
}

export function PlayerBar({ isVisible = false }: PlayerBarProps) {
    const [isPlaying, setIsPlaying] = useState(false);
    const [volume, setVolume] = useState(70);
    const [speed, setSpeed] = useState(1);

    // Mock current track data - in real app this comes from state
    const currentTrack = {
        title: 'Midnight Whispers',
        artist: 'Elena Voight',
        coverImage: 'https://images.unsplash.com/photo-1493225255756-d9584f8606e9?q=80&w=400',
        duration: 1140,
        currentTime: 240
    };

    const speeds = [0.75, 1, 1.25, 1.5];

    const cycleSpeed = () => {
        const currentIndex = speeds.indexOf(speed);
        const nextIndex = (currentIndex + 1) % speeds.length;
        setSpeed(speeds[nextIndex]);
    };

    return (
        <AnimatePresence>
            {isVisible && (
                <motion.div
                    className="fixed bottom-0 left-0 right-0 z-40"
                    initial={{ y: 100, opacity: 0 }}
                    animate={{ y: 0, opacity: 1 }}
                    exit={{ y: 100, opacity: 0 }}
                    transition={{ type: 'spring', damping: 25, stiffness: 300 }}
                >
                    <div className="glass-strong border-t border-white/10 backdrop-blur-2xl">
                        <div className="max-w-[1800px] mx-auto px-6 py-4">
                            <div className="flex items-center justify-between gap-6">
                                {/* Left: Track Info */}
                                <div className="flex items-center gap-4 min-w-0 flex-1">
                                    <div className="relative w-14 h-14 rounded-xl overflow-hidden flex-shrink-0">
                                        <Image
                                            src={currentTrack.coverImage}
                                            alt={currentTrack.title}
                                            fill
                                            className="object-cover"
                                        />
                                    </div>
                                    <div className="min-w-0">
                                        <h4 className="text-sm font-medium text-white truncate">
                                            {currentTrack.title}
                                        </h4>
                                        <p className="text-xs text-zinc-400 truncate">
                                            {currentTrack.artist}
                                        </p>
                                    </div>
                                </div>

                                {/* Center: Controls + Waveform */}
                                <div className="flex-1 max-w-2xl">
                                    <div className="flex items-center gap-4 justify-center mb-2">
                                        <motion.button
                                            className="p-2 rounded-full hover:bg-white/5"
                                            whileHover={{ scale: 1.1 }}
                                            whileTap={{ scale: 0.9 }}
                                        >
                                            <SkipBack className="w-5 h-5 text-zinc-400" strokeWidth={1.5} />
                                        </motion.button>

                                        <motion.button
                                            className="p-3 rounded-full bg-[#00B4D8] hover:shadow-[0_0_20px_rgba(0,180,216,0.5)]"
                                            whileHover={{ scale: 1.1 }}
                                            whileTap={{ scale: 0.9 }}
                                            onClick={() => setIsPlaying(!isPlaying)}
                                        >
                                            {isPlaying ? (
                                                <Pause className="w-5 h-5 text-white" strokeWidth={2} />
                                            ) : (
                                                <Play className="w-5 h-5 text-white" strokeWidth={2} />
                                            )}
                                        </motion.button>

                                        <motion.button
                                            className="p-2 rounded-full hover:bg-white/5"
                                            whileHover={{ scale: 1.1 }}
                                            whileTap={{ scale: 0.9 }}
                                        >
                                            <SkipForward className="w-5 h-5 text-zinc-400" strokeWidth={1.5} />
                                        </motion.button>
                                    </div>

                                    {/* Waveform Visualization */}
                                    <div className="h-12 relative">
                                        <NeonWaveform animate={isPlaying} className="opacity-60" />
                                    </div>
                                </div>

                                {/* Right: Volume + Speed */}
                                <div className="hidden lg:flex items-center gap-4 flex-1 justify-end">
                                    <motion.button
                                        className="px-3 py-1.5 rounded-full glass text-xs font-medium text-[#00B4D8] hover:bg-white/10"
                                        whileHover={{ scale: 1.05 }}
                                        whileTap={{ scale: 0.95 }}
                                        onClick={cycleSpeed}
                                    >
                                        {speed}x
                                    </motion.button>

                                    <div className="flex items-center gap-2">
                                        <Volume2 className="w-4 h-4 text-zinc-400" strokeWidth={1.5} />
                                        <input
                                            type="range"
                                            min="0"
                                            max="100"
                                            value={volume}
                                            onChange={(e) => setVolume(Number(e.target.value))}
                                            className="w-24 h-1 bg-zinc-700 rounded-full appearance-none cursor-pointer
                        [&::-webkit-slider-thumb]:appearance-none
                        [&::-webkit-slider-thumb]:w-3
                        [&::-webkit-slider-thumb]:h-3
                        [&::-webkit-slider-thumb]:rounded-full
                        [&::-webkit-slider-thumb]:bg-[#00B4D8]
                        [&::-webkit-slider-thumb]:cursor-pointer"
                                        />
                                    </div>

                                    <motion.button
                                        className="p-2 rounded-full hover:bg-white/5"
                                        whileHover={{ scale: 1.1 }}
                                        whileTap={{ scale: 0.9 }}
                                    >
                                        <X className="w-4 h-4 text-zinc-400" strokeWidth={1.5} />
                                    </motion.button>
                                </div>
                            </div>
                        </div>
                    </div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}

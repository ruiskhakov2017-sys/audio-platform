'use client';

import { motion } from 'framer-motion';
import { Play } from 'lucide-react';
import Image from 'next/image';
import Link from 'next/link';
import { useState, useEffect } from 'react';
import { getTopStories } from '@/app/actions/catalog';
import { getDisplayTags } from '@/lib/stories';
import type { Story } from '@/types/story';

const DEFAULT_COVER = '/images/custom-order.png';

export function TopSales() {
    const [stories, setStories] = useState<Story[]>([]);
    const [hoveredId, setHoveredId] = useState<number | null>(null);

    useEffect(() => {
        getTopStories(12).then(setStories);
    }, []);

    return (
        <section className="relative py-20 px-6 overflow-hidden">
            <div className="relative z-10 max-w-7xl mx-auto">
                <motion.div
                    className="text-center mb-16"
                    initial={{ opacity: 0, y: 20 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    viewport={{ once: true }}
                    transition={{ duration: 0.6 }}
                >
                    <h2 className="font-heading text-3xl md:text-4xl font-bold text-white mb-4">
                        Топ прослушиваний
                    </h2>
                </motion.div>

                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
                    {stories.length === 0 && (
                        <div className="col-span-full py-12 text-center text-zinc-500">
                            Загрузка...
                        </div>
                    )}
                    {stories.map((story, index) => (
                        <motion.div
                            key={story.id}
                            initial={{ opacity: 0, y: 20 }}
                            whileInView={{ opacity: 1, y: 0 }}
                            viewport={{ once: true }}
                            transition={{ duration: 0.4, delay: index * 0.05 }}
                        >
                            <Link href={`/story/${story.id}`}>
                                <div className="relative aspect-[3/4] rounded-[2rem] overflow-hidden glass-premium hover:border-[#00B4D8]/50 transition-all duration-500 group">
                                    <Image
                                        src={story.coverImage || DEFAULT_COVER}
                                        alt={story.title}
                                        fill
                                        className="object-cover transition-transform duration-700 group-hover:scale-110 opacity-90 group-hover:opacity-100"
                                        unoptimized
                                        sizes="(max-width: 1024px) 50vw, 25vw"
                                        onError={(e) => {
                                            const t = e.target as HTMLImageElement;
                                            if (t?.src && !t.src.includes('default-cover')) t.src = DEFAULT_COVER;
                                        }}
                                    />
                                    <div className="absolute inset-0 bg-gradient-to-t from-[#000814] via-transparent to-transparent opacity-90" />
                                    <div className="absolute inset-0 flex items-center justify-center transition-opacity duration-300 opacity-0 group-hover:opacity-100">
                                        <div className="absolute inset-0 bg-black/20 backdrop-blur-[2px]" />
                                        <motion.div
                                            className="relative z-10 w-16 h-16 rounded-full bg-[#00B4D8] flex items-center justify-center shadow-[0_0_40px_rgba(0,180,216,0.8)]"
                                            whileHover={{ scale: 1.1 }}
                                        >
                                            <Play className="w-8 h-8 text-white ml-1" strokeWidth={2} fill="white" />
                                        </motion.div>
                                    </div>
                                    <div className="absolute top-3 left-3 z-10">
                                        <span className="inline-block px-2 py-0.5 rounded bg-black/60 text-[#00B4D8] text-sm font-bold">
                                            Топ {index + 1}
                                        </span>
                                    </div>
                                    <div className="absolute bottom-0 left-0 right-0 p-6 transform transition-transform duration-300 group-hover:-translate-y-2">
                                        <h3 className="text-lg font-bold text-white mb-1 line-clamp-2 drop-shadow-md">
                                            {story.title}
                                        </h3>
                                        {story.authorName && (
                                            <p className="text-zinc-400 text-sm mb-1 drop-shadow-md">
                                                {story.authorName}
                                            </p>
                                        )}
                                        {getDisplayTags(story)[0] && (
                                            <p className="text-[#00B4D8] text-sm font-medium drop-shadow-[0_0_8px_rgba(0,180,216,0.5)]">
                                                {getDisplayTags(story)[0]}
                                            </p>
                                        )}
                                    </div>
                                    <div className="absolute inset-0 border border-[#00B4D8]/0 group-hover:border-[#00B4D8]/50 rounded-[2rem] transition-colors duration-500 pointer-events-none" />
                                </div>
                            </Link>
                        </motion.div>
                    ))}
                </div>

                <div className="text-center mt-12">
                    <Link
                        href="/browse"
                        className="inline-block px-10 py-4 rounded-full font-medium border-2 border-[#00B4D8] text-[#00B4D8] hover:bg-[#00B4D8]/10 hover:shadow-[0_0_25px_rgba(0,180,216,0.4)] transition-all duration-300"
                    >
                        Перейти в полный каталог
                    </Link>
                </div>
            </div>
        </section>
    );
}

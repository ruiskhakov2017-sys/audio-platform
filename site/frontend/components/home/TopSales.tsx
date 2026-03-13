'use client';

import { motion } from 'framer-motion';
import Image from 'next/image';
import Link from 'next/link';
import { useState, useEffect } from 'react';
import { getTopStories } from '@/app/actions/catalog';
import { getDisplayTags } from '@/lib/stories';
import type { Story } from '@/types/story';

const DEFAULT_COVER = '/images/custom-order.png';

export function TopSales() {
    const [stories, setStories] = useState<Story[]>([]);

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
                            key={`${story.id}-${story.slug}-${index}`}
                            initial={{ opacity: 0, y: 20 }}
                            whileInView={{ opacity: 1, y: 0 }}
                            viewport={{ once: true }}
                            transition={{ duration: 0.4, delay: index * 0.05 }}
                        >
                            <Link href={`/story/${story.id}`}>
                                <div className="relative aspect-[3/4] overflow-hidden rounded-2xl group cursor-pointer transition-all duration-300 hover:-translate-y-2 hover:shadow-[0_0_30px_rgba(0,180,216,0.4)] border border-transparent hover:border-[#00B4D8]/30">
                                    <Image
                                        src={story.coverImage || DEFAULT_COVER}
                                        alt={story.title}
                                        fill
                                        className="object-cover transition-transform duration-500 group-hover:scale-105"
                                        unoptimized
                                        sizes="(max-width: 1024px) 50vw, 25vw"
                                        onError={(e) => {
                                            const t = e.target as HTMLImageElement;
                                            if (t?.src && !t.src.includes('default-cover')) t.src = DEFAULT_COVER;
                                        }}
                                    />

                                    {/* Gradient Overlay */}
                                    <div className="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-black/90 via-black/50 to-transparent" />

                                    {/* Top Badge */}
                                    <div className={`absolute top-3 left-3 px-3 py-1 rounded-lg backdrop-blur-md border font-bold text-sm shadow-lg ${index < 3
                                            ? 'bg-amber-500/80 border-amber-300 text-white shadow-[0_0_10px_rgba(245,158,11,0.5)]'
                                            : 'bg-black/40 border-white/20 text-white'
                                        }`}>
                                        Топ {index + 1}
                                    </div>

                                    {/* Content */}
                                    <div className="absolute bottom-0 left-0 right-0 p-4 flex flex-col gap-1 transform transition-transform duration-300 group-hover:-translate-y-1">
                                        {getDisplayTags(story)[0] && (
                                            <p className="text-[#00B4D8] text-xs font-semibold uppercase tracking-wider mb-1">
                                                {getDisplayTags(story)[0]}
                                            </p>
                                        )}
                                        <h3 className="text-white font-bold text-lg leading-tight line-clamp-2 drop-shadow-md">
                                            {story.title}
                                        </h3>
                                        {story.authorName && (
                                            <p className="text-zinc-400 text-sm line-clamp-1">
                                                {story.authorName}
                                            </p>
                                        )}
                                    </div>
                                </div>
                            </Link>
                        </motion.div>
                    ))}
                </div>

                <div className="text-center mt-12">
                    <Link
                        href="/browse"
                        className="inline-flex items-center justify-center px-8 py-3 rounded-xl font-bold text-lg transition-all duration-300 border-2 border-[#00B4D8] text-[#00B4D8] hover:bg-[#00B4D8] hover:text-black hover:shadow-[0_0_25px_rgba(0,180,216,0.6)] hover:scale-105 active:scale-95 mt-8"
                    >
                        Смотреть весь каталог
                    </Link>
                </div>
            </div>
        </section>
    );
}

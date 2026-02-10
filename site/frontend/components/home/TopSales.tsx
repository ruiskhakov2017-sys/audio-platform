'use client';

import { motion } from 'framer-motion';
import { Play } from 'lucide-react';
import Image from 'next/image';
import Link from 'next/link';
import { useState } from 'react';

const TOP_ITEMS = Array.from({ length: 12 }, (_, i) => ({
    id: i + 1,
    title: `Топ ${i + 1}`,
    coverImage: `/images/top-${i + 1}.jpg`,
    price: i % 2 === 0 ? undefined : 190,
}));

export function TopSales() {
    const [hoveredId, setHoveredId] = useState<number | null>(null);

    return (
        <section className="relative py-20 px-6 overflow-hidden">
            <div className="absolute inset-0 z-0">
                <Image
                    src="/bg4.jpg"
                    alt="background"
                    fill
                    className="object-cover w-full h-full opacity-5 grayscale"
                    quality={50}
                    unoptimized
                />
            </div>
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

                <div className="grid grid-cols-2 lg:grid-cols-4 gap-6">
                    {TOP_ITEMS.map((story, index) => (
                        <motion.div
                            key={story.id}
                            initial={{ opacity: 0, y: 20 }}
                            whileInView={{ opacity: 1, y: 0 }}
                            viewport={{ once: true }}
                            transition={{ duration: 0.4, delay: index * 0.05 }}
                            onMouseEnter={() => setHoveredId(story.id)}
                            onMouseLeave={() => setHoveredId(null)}
                        >
                            <Link href={`/story/${story.id}`}>
                                <div className="relative aspect-[3/4] rounded-[2rem] overflow-hidden glass-premium hover:border-[#00B4D8]/50 transition-all duration-500 group">
                                    <Image
                                        src={story.coverImage}
                                        alt={story.title}
                                        fill
                                        className="object-cover transition-transform duration-700 group-hover:scale-110 opacity-80 group-hover:opacity-100"
                                        unoptimized
                                        sizes="(max-width: 1024px) 50vw, 25vw"
                                    />
                                    <div className="absolute inset-0 bg-gradient-to-t from-[#000814] via-transparent to-transparent opacity-90" />
                                    <div className={`absolute inset-0 flex items-center justify-center transition-opacity duration-300 ${hoveredId === story.id ? 'opacity-100' : 'opacity-0'}`}>
                                        <div className="absolute inset-0 bg-black/20 backdrop-blur-[2px]" />
                                        <motion.div
                                            className="relative z-10 w-16 h-16 rounded-full bg-[#00B4D8] flex items-center justify-center shadow-[0_0_40px_rgba(0,180,216,0.8)]"
                                            initial={{ scale: 0.8 }}
                                            animate={{ scale: hoveredId === story.id ? 1 : 0.8 }}
                                            whileHover={{ scale: 1.1 }}
                                        >
                                            <Play className="w-8 h-8 text-white ml-1" strokeWidth={2} fill="white" />
                                        </motion.div>
                                    </div>
                                    <div className="absolute bottom-0 left-0 right-0 p-6 transform transition-transform duration-300 group-hover:-translate-y-2">
                                        <h3 className="text-lg font-bold text-white mb-2 line-clamp-2 drop-shadow-md">
                                            {story.title}
                                        </h3>
                                        <p className="text-[#00B4D8] font-medium drop-shadow-[0_0_8px_rgba(0,180,216,0.5)]">
                                            {story.price ? `${story.price} ₽` : 'Бесплатно'}
                                        </p>
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

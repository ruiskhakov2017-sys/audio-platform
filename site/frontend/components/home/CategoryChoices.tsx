'use client';

import { motion } from 'framer-motion';
import Link from 'next/link';
import Image from 'next/image';

const categories = [
    { title: 'Восемнадцать лет', description: 'Откровенные истории для взрослых', image: '/images/genres/18-plus.jpg', href: '/browse?genre=Восемнадцать+лет' },
    { title: 'Служебный роман', description: 'Страсть в офисе после заката', image: '/images/genres/office.jpg', href: '/browse?genre=Служебный+роман' },
    { title: 'Группа', description: 'То, что нельзя, но очень хочется', image: '/images/genres/group.jpg', href: '/browse?genre=Группа' },
    { title: 'Подчинение', description: 'Игры власти и подчинения', image: '/images/genres/submission.jpg', href: '/browse?genre=Подчинение' },
    { title: 'Измена', description: 'Случайные встречи, яркие эмоции', image: '/images/genres/betrayal.jpg', href: '/browse?genre=Измена' },
    { title: 'Фантазии', description: 'Другие миры, другие правила', image: '/images/genres/fantasies.jpg', href: '/browse?genre=Фантазии' },
];

export function CategoryChoices() {
    return (
        <section id="genres" className="py-20 px-6">
            <div className="max-w-7xl mx-auto">
                <motion.div
                    className="flex flex-col items-center justify-center text-center max-w-3xl mx-auto mb-12 px-4"
                    initial={{ opacity: 0, y: 20 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    viewport={{ once: true }}
                    transition={{ duration: 0.6 }}
                >
                    <h2 className="text-4xl md:text-5xl font-black bg-clip-text text-transparent bg-gradient-to-r from-[#00B4D8] to-white drop-shadow-[0_0_15px_rgba(0,180,216,0.3)] mb-4">
                        Популярные жанры
                    </h2>
                    <p className="text-lg md:text-xl text-white/70 font-light leading-relaxed">
                        Выбирай, что заводит тебя сегодня
                    </p>
                </motion.div>

                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {categories.map((category, index) => (
                        <motion.div
                            key={category.title}
                            initial={{ opacity: 0, scale: 0.95 }}
                            whileInView={{ opacity: 1, scale: 1 }}
                            viewport={{ once: true }}
                            transition={{ duration: 0.5, delay: index * 0.1 }}
                        >
                            <Link href={category.href}>
                                <motion.div
                                    className="relative aspect-[3/4] rounded-[2.5rem] overflow-hidden group cursor-pointer"
                                    whileHover={{ scale: 1.05 }}
                                    transition={{ duration: 0.3 }}
                                >
                                    {/* Image */}
                                    <div className="absolute inset-0">
                                        <motion.div
                                            className="w-full h-full"
                                            initial={{ filter: 'grayscale(100%)' }}
                                            whileInView={{ filter: 'grayscale(0%)' }}
                                            viewport={{ margin: "-20%" }}
                                            transition={{ duration: 0.5 }}
                                        >
                                            <Image
                                                src={category.image}
                                                alt={category.title}
                                                fill
                                                className="object-cover transition-transform duration-500 md:grayscale md:group-hover:grayscale-0 group-hover:scale-110"
                                                unoptimized
                                                sizes="(max-width: 768px) 100vw, (max-width: 1200px) 50vw, 33vw"
                                            />
                                        </motion.div>
                                    </div>

                                    {/* Gradient Overlay */}
                                    <div className="absolute inset-0 bg-gradient-to-t from-black via-black/50 to-transparent opacity-80 group-hover:opacity-60 transition-opacity duration-300" />
                                    {/* Bottom Overlay for Readability */}
                                    <div className="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-black/90 via-black/40 to-transparent pointer-events-none" />

                                    {/* Content */}
                                    <div className="absolute bottom-0 left-0 right-0 p-8 pb-6 transform transition-transform duration-300 group-hover:scale-105 origin-bottom-left">
                                        <h3 className="text-xl md:text-2xl font-bold text-white mb-2 drop-shadow-lg">
                                            {category.title}
                                        </h3>
                                        <p className="text-zinc-200 text-sm font-medium drop-shadow-md">
                                            {category.description}
                                        </p>
                                    </div>
                                </motion.div>
                            </Link>
                        </motion.div>
                    ))}
                </div>
            </div>
        </section>
    );
}

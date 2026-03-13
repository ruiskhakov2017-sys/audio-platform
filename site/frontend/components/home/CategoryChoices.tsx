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
                                <div className="relative w-full h-[350px] md:h-[500px] overflow-hidden rounded-3xl group cursor-pointer transition-all duration-500 hover:-translate-y-2 shadow-[0_0_20px_rgba(0,180,216,0.3)] md:shadow-none hover:shadow-[0_0_40px_rgba(0,180,216,0.3)] bg-black">
                                    {/* Image */}
                                    <Image
                                        src={category.image}
                                        alt={category.title}
                                        fill
                                        className="absolute inset-0 w-full h-full object-cover grayscale-0 md:grayscale md:group-hover:grayscale-0 transition-all duration-700 group-hover:scale-110"
                                        unoptimized
                                        sizes="(max-width: 768px) 100vw, (max-width: 1200px) 50vw, 33vw"
                                    />

                                    {/* Gradient Overlay */}
                                    <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/40 to-transparent" />

                                    {/* Content */}
                                    <div className="absolute inset-0 flex flex-col items-center justify-end pb-12 px-6 text-center">
                                        <h3 className="text-3xl md:text-3xl font-black text-white drop-shadow-lg transition-transform duration-500 group-hover:-translate-y-2 group-hover:text-[#00B4D8]">
                                            {category.title}
                                        </h3>
                                        <p className="text-lg md:text-base text-white/70 mt-3 font-medium transition-all duration-500 group-hover:text-white group-hover:drop-shadow-[0_0_10px_rgba(255,255,255,0.5)] transform translate-y-2 group-hover:translate-y-0">
                                            {category.description}
                                        </p>
                                    </div>
                                </div>
                            </Link>
                        </motion.div>
                    ))}
                </div>
            </div>
        </section>
    );
}

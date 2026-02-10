import {
  Inter,
  Montserrat,
  Playfair_Display,
  Cormorant_Garamond,
  Raleway,
  Oswald,
  Tenor_Sans,
  Yeseva_One,
  Philosopher,
  Forum,
  Oranienbaum,
  Old_Standard_TT,
  Comfortaa,
  Unbounded,
  Alice,
  Ruslan_Display,
  Kelly_Slab,
  Marck_Script,
  Bad_Script,
  Lobster,
} from 'next/font/google';

const inter = Inter({ subsets: ['cyrillic', 'latin'] });
const montserrat = Montserrat({ subsets: ['cyrillic', 'latin'], weight: ['400', '600', '700'] });
const playfair = Playfair_Display({ subsets: ['cyrillic', 'latin'], weight: ['400', '600', '700'] });
const cormorant = Cormorant_Garamond({ subsets: ['cyrillic', 'latin'], weight: ['400', '600', '700'] });
const raleway = Raleway({ subsets: ['cyrillic', 'latin'], weight: ['400', '600', '700'] });
const oswald = Oswald({ subsets: ['cyrillic', 'latin'], weight: ['400', '600', '700'] });
const tenorSans = Tenor_Sans({ subsets: ['cyrillic', 'latin'], weight: '400' });
const yeseva = Yeseva_One({ subsets: ['cyrillic', 'latin'], weight: '400' });
const philosopher = Philosopher({ subsets: ['cyrillic', 'latin'], weight: ['400', '700'] });
const forum = Forum({ subsets: ['cyrillic', 'latin'], weight: '400' });
const oranienbaum = Oranienbaum({ subsets: ['cyrillic', 'latin'], weight: '400' });
const oldStandard = Old_Standard_TT({ subsets: ['cyrillic', 'latin'], weight: ['400', '700'] });
const comfortaa = Comfortaa({ subsets: ['cyrillic', 'latin'], weight: ['400', '600', '700'] });
const unbounded = Unbounded({ subsets: ['cyrillic', 'latin'], weight: ['400', '600', '700'] });
const alice = Alice({ subsets: ['cyrillic', 'latin'], weight: '400' });
const ruslan = Ruslan_Display({ subsets: ['cyrillic', 'latin'], weight: '400' });
const kellySlab = Kelly_Slab({ subsets: ['cyrillic', 'latin'], weight: '400' });
const marckScript = Marck_Script({ subsets: ['cyrillic', 'latin'], weight: '400' });
const badScript = Bad_Script({ subsets: ['cyrillic', 'latin'], weight: '400' });
const lobster = Lobster({ subsets: ['cyrillic', 'latin'], weight: '400' });

const fonts = [
  { id: 1, name: 'Inter', desc: 'Нейтральный', font: inter },
  { id: 2, name: 'Montserrat', desc: 'Геометрический', font: montserrat },
  { id: 3, name: 'Playfair Display', desc: 'Классика', font: playfair },
  { id: 4, name: 'Cormorant Garamond', desc: 'Элегантный', font: cormorant },
  { id: 5, name: 'Raleway', desc: 'Изящный санс', font: raleway },
  { id: 6, name: 'Oswald', desc: 'Высокий, жирный', font: oswald },
  { id: 7, name: 'Tenor Sans', desc: 'Фешн, для заголовков', font: tenorSans },
  { id: 8, name: 'Yeseva One', desc: 'Женственный, скругленный', font: yeseva },
  { id: 9, name: 'Philosopher', desc: 'Плавные линии', font: philosopher },
  { id: 10, name: 'Forum', desc: 'Античный, римский', font: forum },
  { id: 11, name: 'Oranienbaum', desc: 'Строгий, царский', font: oranienbaum },
  { id: 12, name: 'Old Standard TT', desc: 'Книжный', font: oldStandard },
  { id: 13, name: 'Comfortaa', desc: 'Мягкий, округлый', font: comfortaa },
  { id: 14, name: 'Unbounded', desc: 'Современный, широкий', font: unbounded },
  { id: 15, name: 'Alice', desc: 'Причудливый', font: alice },
  { id: 16, name: 'Ruslan Display', desc: 'Древнерусский стиль', font: ruslan },
  { id: 17, name: 'Kelly Slab', desc: 'Технологичный, квадратный', font: kellySlab },
  { id: 18, name: 'Marck Script', desc: 'Рукописный, фломастер', font: marckScript },
  { id: 19, name: 'Bad Script', desc: 'Рукописный, небрежный', font: badScript },
  { id: 20, name: 'Lobster', desc: 'Игривый, рукописный', font: lobster },
];

const headline = 'Твои скрытые фантазии теперь в звуке';
const paragraph =
  'Коллекция откровенных аудиорассказов. Закрой глаза и позволь воображению нарисовать то, что видео никогда не покажет. Анонимно. Чувственно. Только для тебя.';

export default function FontsGalleryPage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <div className="max-w-4xl mx-auto py-10 px-4">
        <h1 className="text-2xl font-bold text-white mb-10 text-center">
          Примерка шрифтов (20 вариантов)
        </h1>

        <div className="space-y-0">
          {fonts.map((item) => (
            <section
              key={item.id}
              className="pb-8 mb-8 border-b border-gray-800 last:border-b-0 last:mb-0"
            >
              <p className="text-sm text-gray-500 mb-3">
                Вариант #{item.id}: {item.name} ({item.desc})
              </p>
              <h2 className={`${item.font.className} text-3xl md:text-4xl font-semibold text-white mb-4`}>
                {headline}
              </h2>
              <p className={`${item.font.className} text-lg text-gray-300 leading-relaxed`}>
                {paragraph}
              </p>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}

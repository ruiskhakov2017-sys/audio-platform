export default function BrowseLoading() {
  return (
    <div className="min-h-screen pt-24 px-6 pb-12">
      <div className="max-w-[1800px] mx-auto">
        <div className="h-10 w-64 bg-white/10 rounded-lg animate-pulse mb-4" />
        <div className="h-5 w-32 bg-white/5 rounded mb-8" />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="aspect-[3/4] rounded-[2rem] bg-white/5 animate-pulse" />
          ))}
        </div>
      </div>
    </div>
  );
}

import { Hero } from './components/Hero';
import { Features } from './components/Features';
import { Security } from './components/Security';
import { Footer } from './components/Footer';

function App() {
  return (
    <main className="min-h-screen">
      <Hero />
      <Features />
      <Security />
      <Footer />
    </main>
  );
}

export default App;

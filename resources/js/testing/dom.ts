// jsdom implements no scrolling, and the router scrolls on every navigation.
if (typeof window !== 'undefined') window.scrollTo = () => {}

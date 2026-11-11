// Placeholder until the landing page (M11 step 3).
import "../styles.css";
const base = import.meta.env.BASE_URL;
document.getElementById("root")!.innerHTML =
  `<main style="max-width:640px;margin:96px auto;padding:0 16px"><h1>SentinelAI</h1>` +
  `<p><a style="color:#3DDC84" href="${base}app/demo/">Live demo</a> · <a style="color:#3DDC84" href="${base}app/">App</a></p></main>`;

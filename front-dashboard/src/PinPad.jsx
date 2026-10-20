import { useState } from "react";

/**
 * Pavé numérique du terminal (surface d'attaque physique).
 *
 * Pourquoi il est dans le dashboard et non dans le firmware : le clavier du
 * terminal ne parle pas à l'ESP32 (cf. docs/hardware/README.md, option A). Un
 * pavé USB ou ce pavé à l'écran produit exactement les mêmes observations pour
 * le ML (`pin_failure_ratio`, `inter_arrival_median`) sans démonter le
 * terminal la veille de la soutenance.
 *
 * Ce que ce composant ne peut PAS faire : déclencher une remédiation. Un PIN
 * correct autorise seulement une transaction de démonstration, qui passe par
 * /tx/submit comme n'importe quel client.
 */
export default function PinPad({ apiBase, onTransaction }) {
  const [digits, setDigits] = useState("");
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  const press = (digit) => {
    if (digits.length >= 4) return;
    setDigits(digits + digit);
    setMessage(null);
  };

  const submit = async () => {
    if (digits.length !== 4 || busy) return;
    setBusy(true);
    try {
      const response = await fetch(`${apiBase}/pin/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin: digits, amount: 1 }),
      });
      if (!response.ok) {
        setMessage({ ok: false, text: "CODE REFUSÉ" });
        setDigits("");
        return;
      }
      // PIN accepté : on paie pour de vrai (transaction de démonstration).
      const decoys = await fetch(`${apiBase}/decoys?count=10`).then((r) => r.json());
      const pool = await fetch(`${apiBase}/outputs?unspent=true`).then((r) => r.json());
      const real = pool.outputs?.[Math.floor(Math.random() * (pool.outputs?.length || 1))];
      const tx = await fetch(`${apiBase}/tx/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ real_output_id: real?.id, decoy_ids: decoys.decoys }),
      });
      const body = await tx.json();
      if (tx.ok) {
        setMessage({ ok: true, text: `PAIEMENT CONFIRMÉ · montant ${body.amount}` });
        onTransaction?.(body);
      } else {
        setMessage({ ok: false, text: `ÉCHEC : ${body.detail ?? tx.status}` });
      }
    } catch {
      setMessage({ ok: false, text: "API INJOIGNABLE" });
    } finally {
      setBusy(false);
      setDigits("");
    }
  };

  return (
    <div>
      <div className="flex items-center justify-center gap-2 mb-2">
        {[0, 1, 2, 3].map((index) => (
          <span
            key={index}
            className="w-4 h-4 rounded-full border-2 border-ink"
            style={{ backgroundColor: digits.length > index ? "#261E48" : "transparent" }}
          />
        ))}
      </div>
      <div className="grid grid-cols-3 gap-1.5">
        {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((digit) => (
          <button
            key={digit}
            onClick={() => press(String(digit))}
            className="bg-page text-ink font-bold rounded py-1.5 text-sm hover:bg-white"
          >
            {digit}
          </button>
        ))}
        <button
          onClick={() => setDigits("")}
          className="bg-page text-ink/70 font-bold rounded py-1.5 text-xs hover:bg-white"
        >
          C
        </button>
        <button
          onClick={() => press("0")}
          className="bg-page text-ink font-bold rounded py-1.5 text-sm hover:bg-white"
        >
          0
        </button>
        <button
          onClick={submit}
          disabled={digits.length !== 4 || busy}
          className="bg-ink text-white font-bold rounded py-1.5 text-xs disabled:opacity-40"
        >
          OK
        </button>
      </div>
      {message && (
        <p
          className={`mt-2 text-center text-xs font-bold ${
            message.ok ? "text-emerald" : "text-crimson"
          }`}
        >
          {message.text}
        </p>
      )}
      <p className="mt-2 text-center text-ink/50 text-[11px]">
        code du terminal : voir <code>TERMINAL_PIN</code> dans <code>.env</code> ·
        un refus alimente le ML
      </p>
    </div>
  );
}

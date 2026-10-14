// Privy wallet connect for crbi.io, bundled by build.mjs into web/wallet/privy-wallet.js.
// The page imports this file only when a visitor reaches for the Connect button.
import { Component, createElement as h, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { PrivyProvider, usePrivy } from '@privy-io/react-auth';
import { toSolanaWalletConnectors, useWallets } from '@privy-io/react-auth/solana';

/**
 * Mount Privy in a detached root and report the wallet state back to the page.
 * @param {{appId: string, config: object, onState: Function, onError: Function}} options
 * @returns {{login: Function, logout: Function}} filled in once Privy is ready
 */
export function mountWallet({ appId, config, onState, onError }) {
  const api = { login: null, logout: null };

  function Bridge() {
    const { ready, authenticated, login, logout, user } = usePrivy();
    const { wallets } = useWallets();
    api.login = login;
    api.logout = logout;
    useEffect(() => {
      if (!ready) return;
      const linked = user && user.wallet && user.wallet.chainType === 'solana' ? user.wallet.address : null;
      const address = authenticated ? ((wallets[0] && wallets[0].address) || linked) : null;
      onState({ ready, authenticated, address });
    }, [ready, authenticated, wallets, user]);
    return null;
  }

  // If Privy throws (wrong App ID, origin not on the allow list), tell the page instead of hanging.
  class Guard extends Component {
    constructor(props) { super(props); this.state = { failed: false }; }
    static getDerivedStateFromError() { return { failed: true }; }
    componentDidCatch(err) { onError(err); }
    render() { return this.state.failed ? null : this.props.children; }
  }

  const mount = document.createElement('div');
  mount.id = 'crab-wallet-root';
  document.body.appendChild(mount);
  createRoot(mount).render(
    h(Guard, null,
      h(PrivyProvider, {
        appId,
        config: { ...config, externalWallets: { solana: { connectors: toSolanaWalletConnectors() } } },
      }, h(Bridge))),
  );
  return api;
}

import mnemonic
import bip32utils
import requests
import logging
import time
import os
import argparse
import concurrent.futures
import itertools
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()
logging.basicConfig(level=logging.INFO, format='%(message)s', handlers=[RichHandler()])
logger = logging.getLogger("rich")


DERIVATION_PATHS = [
    # BIP44 - Legacy
    "m/44'/0'/0'/0/0",
    # BIP49 - Segwit
    "m/49'/0'/0'/0/0",
    # BIP84 - Native Segwit
    "m/84'/0'/0'/0/0",
    # BIP86 - Taproot
    "m/86'/0'/0'/0/0"
]

class WalletRecoveryTool:
    def __init__(self, api_key=None, max_workers=4):
        self.mnemo = mnemonic.Mnemonic("english")
        self.wordlist = self.mnemo.wordlist
        self.max_workers = max_workers
        self.api_key = api_key
        self.results = []
        
    def clear_console(self):
        """Clear the console screen."""
        if os.name == 'nt':
            os.system('cls')
        else:
            os.system('clear')
            
    def generate_mnemonic(self, word_count=12):
        """Generate a random mnemonic phrase with specified word count (12 or 24)."""
        if word_count == 12:
            strength = 128
        elif word_count == 24:
            strength = 256
        else:
            raise ValueError("Word count must be either 12 or 24")
        
        return self.mnemo.generate(strength=strength)
    
    def is_valid_mnemonic(self, mnemonic_phrase):
        """Check if a mnemonic phrase is valid."""
        try:
            return self.mnemo.check(mnemonic_phrase)
        except Exception:
            return False
    
    def derive_wallet_address(self, mnemonic_phrase, path):
        """Derive a wallet address from a mnemonic phrase using a specific derivation path."""
        try:
            seed = mnemonic.Mnemonic.to_seed(mnemonic_phrase)
            root_key = bip32utils.BIP32Key.fromEntropy(seed)
            
            path_components = path.split('/')
            if path_components[0] == 'm':
                path_components = path_components[1:]
                
            child_key = root_key
            for component in path_components:
                if "'" in component:
                    index = int(component.replace("'", "")) | bip32utils.BIP32_HARDEN
                else:
                    index = int(component)
                child_key = child_key.ChildKey(index)
                
            return child_key.Address()
        except Exception as e:
            logger.error(f"Error deriving address: {str(e)}")
            return None
    
    def check_BTC_balance(self, address, retries=3, delay=5):
        """Check the balance of a Bitcoin address."""
        for attempt in range(retries):
            try:
                url = f"https://blockchain.info/balance?active={address}"
                if self.api_key:
                    url += f"&api_key={self.api_key}"
                
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                balance = data[address]["final_balance"]
                return balance / 100000000
            except requests.RequestException as e:
                if attempt < retries - 1:
                    logger.error(f"Error checking balance, retrying in {delay} seconds: {str(e)}")
                    time.sleep(delay)
                else:
                    logger.error(f"Error checking balance: {str(e)}")
                    
                    if "429" in str(e):
                        delay *= 2
            except (KeyError, ValueError) as e:
                logger.error(f"Error parsing balance response: {str(e)}")
                
        return 0
    
    def check_address_with_paths(self, mnemonic_phrase, paths=None):
        """Check a mnemonic phrase against multiple derivation paths."""
        if paths is None:
            paths = DERIVATION_PATHS
            
        results = []
        
        for path in paths:
            address = self.derive_wallet_address(mnemonic_phrase, path)
            if not address:
                continue
                
            balance = self.check_BTC_balance(address)
            results.append({
                "mnemonic": mnemonic_phrase,
                "address": address,
                "balance": balance,
                "path": path
            })
            
            if balance > 0:
                self.save_wallet_details(mnemonic_phrase, address, balance, path)
                
        return results
    
    def recover_from_partial_mnemonic(self, partial_mnemonic, expected_length=12):
        """Recover a wallet from a partial mnemonic by brute-forcing missing words."""
        partial_words = partial_mnemonic.split()
        missing_words_count = expected_length - len(partial_words)
        
        if missing_words_count <= 0:
            logger.error(f"You provided {len(partial_words)} words. Expected {expected_length} or fewer words.")
            return None
        
        if missing_words_count > 3:
            logger.error("Cannot brute-force more than 3 missing words due to computational limitations.")
            return None
            
        logger.info(f"Attempting to recover a {expected_length}-word wallet with {missing_words_count} missing words.")
        
        if missing_words_count == 1:
            self.brute_force_single_word(partial_words, expected_length)
        elif missing_words_count == 2:
            positions = self.get_missing_positions(expected_length, 2)
            self.brute_force_multiple_words(partial_words, positions, 2)
        elif missing_words_count == 3:
            positions = self.get_missing_positions(expected_length, 3)
            self.brute_force_multiple_words(partial_words, positions, 3)
        
        return self.results
    
    def get_missing_positions(self, total_length, missing_count):
        """Get positions for missing words from user."""
        console.print(f"For a {total_length}-word phrase with {missing_count} missing words, please specify:")
        
        positions = []
        for i in range(missing_count):
            while True:
                try:
                    pos = int(input(f"Position of missing word #{i+1} (1-{total_length}): "))
                    if 1 <= pos <= total_length and pos not in positions:
                        positions.append(pos)
                        break
                    else:
                        console.print("[red]Invalid position. Try again.[/red]")
                except ValueError:
                    console.print("[red]Please enter a valid number.[/red]")
        
        return [p-1 for p in positions]
        
    def brute_force_single_word(self, partial_words, expected_length):
        """Brute force a single missing word in any position."""
        with Progress() as progress:
            for position in range(expected_length + 1):
                task = progress.add_task(f"[cyan]Testing word at position {position+1}...", total=len(self.wordlist))
                
                for word in self.wordlist:
                    test_words = partial_words.copy()
                    test_words.insert(position, word)
                    
                    if len(test_words) == expected_length:
                        test_phrase = ' '.join(test_words)
                        
                        if self.is_valid_mnemonic(test_phrase):
                            results = self.check_address_with_paths(test_phrase)
                            
                            for result in results:
                                if result["balance"] > 0:
                                    self.results.append(result)
                    
                    progress.update(task, advance=1)
    
    def brute_force_multiple_words(self, partial_words, positions, missing_count):
        """Brute force multiple missing words at specific positions."""
        total_combinations = len(self.wordlist) ** missing_count
        
        with Progress() as progress:
            task = progress.add_task(f"[cyan]Testing {missing_count} missing words...", total=total_combinations)
            
            checked = 0
            for word_combo in itertools.product(self.wordlist, repeat=missing_count):
                test_words = partial_words.copy()
                for i, pos in enumerate(positions):
                    test_words.insert(pos, word_combo[i])
                
                test_phrase = ' '.join(test_words)
                
                if self.is_valid_mnemonic(test_phrase):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                        future_to_path = {
                            executor.submit(self.check_address_with_path, test_phrase, path): path 
                            for path in DERIVATION_PATHS
                        }
                        
                        for future in concurrent.futures.as_completed(future_to_path):
                            result = future.result()
                            if result and result["balance"] > 0:
                                self.results.append(result)
                
                checked += 1
                if checked % 100 == 0:
                    progress.update(task, completed=checked)
    
    def check_address_with_path(self, mnemonic_phrase, path):
        """Check a single address with a specific derivation path."""
        address = self.derive_wallet_address(mnemonic_phrase, path)
        if not address:
            return None
            
        balance = self.check_BTC_balance(address)
        result = {
            "mnemonic": mnemonic_phrase,
            "address": address,
            "balance": balance,
            "path": path
        }
        
        if balance > 0:
            self.save_wallet_details(mnemonic_phrase, address, balance, path)
            
        return result
    
    def check_random_wallets(self, count=float('inf'), word_count=12):
        """Check random wallets until a non-zero balance is found or count is reached."""
        mnemonic_count = 0
        
        with Progress() as progress:
            task = progress.add_task("[cyan]Testing random wallets...", total=None)
            
            while mnemonic_count < count:
                mnemonic_phrase = self.generate_mnemonic(word_count)
                results = self.check_address_with_paths(mnemonic_phrase)
                
                for result in results:
                    if result["balance"] > 0:
                        logger.info(f"Found wallet with non-zero balance: {result['balance']} BTC")
                        logger.info(f"Mnemonic: {result['mnemonic']}")
                        logger.info(f"Address: {result['address']}")
                        logger.info(f"Path: {result['path']}")
                
                mnemonic_count += 1
                progress.update(task, description=f"[cyan]Testing random wallets: {mnemonic_count} checked")
                
                if mnemonic_count % 10 == 0:
                    logger.info(f"Total Mnemonic Phrases tested: {mnemonic_count}")
    
    def save_wallet_details(self, mnemonic_phrase, address, balance, path=None):
        """Save discovered wallet details to a file."""
        with open("found_wallets.txt", "a") as f:
            f.write(f"Mnemonic Phrase: {mnemonic_phrase}\n")
            f.write(f"Wallet Address: {address}\n")
            f.write(f"Balance: {balance} BTC\n")
            if path:
                f.write(f"Derivation Path: {path}\n")
            f.write("\n")
            
        # Log the finding
        logger.info(f"[bold green]Found wallet with {balance} BTC![/bold green]")
        logger.info(f"Details saved to found_wallets.txt")
    
    def recover_by_address(self, target_address, word_count=12, partial_mnemonic=None):
        """Attempt to recover a mnemonic by searching for a specific wallet address."""
        if partial_mnemonic:
            logger.info(f"Attempting to recover wallet for address {target_address} using partial mnemonic.")
            partial_words = partial_mnemonic.split()
            missing_words_count = word_count - len(partial_words)
            
            if missing_words_count <= 0:
                logger.error(f"You provided {len(partial_words)} words. Expected {word_count} or fewer words.")
                return None
            
            if missing_words_count > 3:
                logger.error("Cannot brute-force more than 3 missing words due to computational limitations.")
                return None
                
            logger.info(f"Attempting to recover a {word_count}-word wallet with {missing_words_count} missing words.")
            
            if missing_words_count == 1:
                self.brute_force_single_word_by_address(partial_words, word_count, target_address)
            elif missing_words_count == 2:
                positions = self.get_missing_positions(word_count, 2)
                self.brute_force_multiple_words_by_address(partial_words, positions, 2, target_address)
            elif missing_words_count == 3:
                positions = self.get_missing_positions(word_count, 3)
                self.brute_force_multiple_words_by_address(partial_words, positions, 3, target_address)
        else:
            logger.error("Recovering without any partial mnemonic is computationally infeasible.")
            logger.info("Please provide at least some words from your mnemonic phrase.")
        
        return self.results
    
    def brute_force_single_word_by_address(self, partial_words, expected_length, target_address):
        """Brute force a single missing word for a specific address."""
        with Progress() as progress:
            for position in range(expected_length + 1):
                task = progress.add_task(f"[cyan]Testing word at position {position+1}...", total=len(self.wordlist))
                
                for word in self.wordlist:
                    test_words = partial_words.copy()
                    test_words.insert(position, word)
                    
                    if len(test_words) == expected_length:
                        test_phrase = ' '.join(test_words)
                        
                        if self.is_valid_mnemonic(test_phrase):
                            match_found = self.check_for_address_match(test_phrase, target_address)
                            if match_found:
                                logger.info(f"[bold green]Found matching mnemonic for address {target_address}![/bold green]")
                                logger.info(f"Mnemonic: {test_phrase}")
                                logger.info(f"Path: {match_found['path']}")
                                self.results.append(match_found)
                    
                    progress.update(task, advance=1)
    
    def brute_force_multiple_words_by_address(self, partial_words, positions, missing_count, target_address):
        """Brute force multiple missing words for a specific address."""
        total_combinations = len(self.wordlist) ** missing_count
        
        with Progress() as progress:
            task = progress.add_task(f"[cyan]Testing {missing_count} missing words...", total=total_combinations)
            
            checked = 0
            for word_combo in itertools.product(self.wordlist, repeat=missing_count):
                test_words = partial_words.copy()
                for i, pos in enumerate(positions):
                    test_words.insert(pos, word_combo[i])
                
                test_phrase = ' '.join(test_words)
                
                if self.is_valid_mnemonic(test_phrase):
                    match_found = self.check_for_address_match(test_phrase, target_address)
                    if match_found:
                        logger.info(f"[bold green]Found matching mnemonic for address {target_address}![/bold green]")
                        logger.info(f"Mnemonic: {test_phrase}")
                        logger.info(f"Path: {match_found['path']}")
                        self.results.append(match_found)
                
                checked += 1
                if checked % 100 == 0:
                    progress.update(task, completed=checked)
    
    def check_for_address_match(self, mnemonic_phrase, target_address):
        """Check if a mnemonic phrase generates the target address in any derivation path."""
        for path in DERIVATION_PATHS:
            address = self.derive_wallet_address(mnemonic_phrase, path)
            if not address:
                continue
                
            if address == target_address:
                balance = self.check_BTC_balance(address)
                result = {
                    "mnemonic": mnemonic_phrase,
                    "address": address,
                    "balance": balance,
                    "path": path
                }
                
                self.save_wallet_details(mnemonic_phrase, address, balance, path)
                return result
                
        return None
    
    def display_help(self):
        """Display a help message with commands."""
        help_text = """
        Bitcoin Wallet Recovery Tool
        ---------------------------
        
        Commands:
        1. Check random wallets - Generate and check random wallets
        2. Verify a mnemonic - Check if a mnemonic is valid and has balance
        3. Recover using wallet address - Find mnemonic that generates a specific wallet address
        4. Exit - Exit the program
        
        For recovery using a wallet address, you need:
        - Your Bitcoin wallet address
        - Some words from your mnemonic phrase (can recover up to 3 missing words)
        
        The tool supports both 12 and 24-word mnemonic phrases.
        """
        console.print(Panel(help_text, title="Help", border_style="green"))
    
    def display_results(self):
        """Display results in a formatted table."""
        if not self.results:
            console.print("[yellow]No wallets with balance found.[/yellow]")
            return
            
        table = Table(show_header=True, header_style="bold cyan", box=box.ROUNDED)
        table.add_column("Mnemonic", style="dim", no_wrap=False)
        table.add_column("Address")
        table.add_column("Balance", justify="right")
        table.add_column("Path")
        
        for result in self.results:
            table.add_row(
                result["mnemonic"], 
                result["address"], 
                f"{result['balance']} BTC", 
                result["path"]
            )
            
        console.print(Panel(table, title="Found Wallets", border_style="green"))
    
    def display_donation_info(self):
        """Display donation information."""
        donation_text = """
        If you found this tool helpful, please consider donating:
        
        • BTC: bc1qprlv7yphulfuaxc0lqveu5y2vsrrc5w0fsa3gg
        • ETH: 0xB1d3b0A9CF92b9262182C14Fa6b0B3E2Ce469CBf
        
        Thank you for your support!
        """
        console.print(Panel(donation_text, title="Support the Project", border_style="yellow"))

    def run(self):
        """Run the interactive recovery tool."""
        console.print(Panel("[bold green]Bitcoin Wallet Recovery Tool[/bold green]", border_style="green"))
        
        while True:
            console.print("\n[bold cyan]Choose an option:[/bold cyan]")
            console.print("1. Recover from partial mnemonic")
            console.print("2. Verify a mnemonic")
            console.print("3. Check random wallets")
            console.print("4. Exit")
            
            choice = input("\nEnter your choice (1-4): ")
            
            self.clear_console()
            
            if choice == "3":
                word_count = input("Enter mnemonic length to generate (12 or 24): ")
                try:
                    word_count = int(word_count)
                    if word_count not in [12, 24]:
                        raise ValueError
                except ValueError:
                    logger.error("Invalid mnemonic length. Using default of 12.")
                    word_count = 12
                    
                count = input("Enter number of wallets to check (leave blank for infinite): ")
                try:
                    count = int(count) if count.strip() else float('inf')
                except ValueError:
                    logger.error("Invalid count. Running in infinite mode.")
                    count = float('inf')
                    
                self.check_random_wallets(count, word_count)
                
            elif choice == "2":
                mnemonic_phrase = input("Enter the full mnemonic phrase to verify: ")
                if not self.is_valid_mnemonic(mnemonic_phrase):
                    logger.error("Invalid mnemonic phrase.")
                else:
                    results = self.check_address_with_paths(mnemonic_phrase)
                    self.results.extend([r for r in results if r["balance"] > 0])
                    self.display_results()
            
            elif choice == "1":
                target_address = input("Enter your Bitcoin wallet address: ")
                if not target_address:
                    logger.error("No address provided.")
                    continue
                    
                word_count = input("Enter expected mnemonic length (12 or 24): ")
                try:
                    word_count = int(word_count)
                    if word_count not in [12, 24]:
                        raise ValueError
                except ValueError:
                    logger.error("Invalid mnemonic length. Using default of 12.")
                    word_count = 12
                
                partial_mnemonic = input("Enter the words you remember from your mnemonic phrase, separated by spaces: ")
                if not partial_mnemonic:
                    logger.error("Recovery without any mnemonic words is not feasible.")
                    logger.info("Please provide at least some words from your mnemonic phrase.")
                    continue
                    
                self.recover_by_address(target_address, word_count, partial_mnemonic)
                self.display_results()
                    
            elif choice == "4":
                console.print("[bold green]Thank you for using the Bitcoin Wallet Recovery Tool![/bold green]")
                self.display_donation_info()
                break
                
            else:
                logger.error("Invalid choice. Please try again.")
                self.display_help()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bitcoin Wallet Recovery Tool")
    parser.add_argument("--api-key", help="API key for blockchain.info", default=None)
    parser.add_argument("--workers", type=int, help="Number of worker threads", default=4)
    parser.add_argument("--batch", help="Run in batch mode with a provided mnemonic file", default=None)
    parser.add_argument("--address", help="Target Bitcoin address to recover", default=None)
    
    args = parser.parse_args()
    
    tool = WalletRecoveryTool(api_key=args.api_key, max_workers=args.workers)
    
    if args.batch:
        try:
            with open(args.batch, 'r') as f:
                for line in f:
                    mnemonic_phrase = line.strip()
                    if mnemonic_phrase and tool.is_valid_mnemonic(mnemonic_phrase):
                        tool.check_address_with_paths(mnemonic_phrase)
        except FileNotFoundError:
            logger.error(f"File not found: {args.batch}")
    elif args.address:
        logger.info(f"Recovery mode for address: {args.address}")
        partial = input("Enter partial mnemonic (words you remember): ")
        word_count = int(input("Enter expected mnemonic length (12 or 24): ") or "12")
        tool.recover_by_address(args.address, word_count, partial)
        tool.display_results()
    else:
        tool.run()
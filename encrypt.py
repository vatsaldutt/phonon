# Simple XOR encryption
def xor_encrypt_decrypt(data: bytes, key: int) -> bytes:
    return bytes([b ^ key for b in data])

# Convert image to encrypted text
def image_to_encrypted_text(image_path: str, output_txt: str, key: int):
    with open(image_path, 'rb') as img_file:
        img_bytes = img_file.read()

    encrypted_bytes = xor_encrypt_decrypt(img_bytes, key)

    with open(output_txt, 'w') as txt_file:
        txt_file.write(' '.join([str(b) for b in encrypted_bytes]))

    print(f"Encrypted data written to {output_txt}")

# Reconstruct image from encrypted text
def encrypted_text_to_image(input_txt: str, output_image_path: str, key: int):
    with open(input_txt, 'r') as txt_file:
        encrypted_str = txt_file.read()

    encrypted_bytes = bytes([int(b) for b in encrypted_str.strip().split()])
    decrypted_bytes = xor_encrypt_decrypt(encrypted_bytes, key)

    with open(output_image_path, 'wb') as img_file:
        img_file.write(decrypted_bytes)

    print(f"Decrypted image written to {output_image_path}")

# Example usage
if __name__ == "__main__":
    original_image = "qwen2.jpeg"
    encrypted_file = "qwen2.txt"
    reconstructed_image = "nice_again.png"
    key = 123  # Your encryption key (0-255)

    image_to_encrypted_text(original_image, encrypted_file, key)
    # encrypted_text_to_image(encrypted_file, reconstructed_image, key)
